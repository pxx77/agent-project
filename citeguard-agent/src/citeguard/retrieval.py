from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Protocol

from .config import Settings
from .embeddings import Embedder, build_embedder, cosine
from .models import Chunk

_WORD = re.compile(r"[a-z0-9]{2,}")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

#: Reciprocal-rank-fusion damping constant from Cormack et al. (2009). Large enough
#: that a chunk ranked first by one channel cannot be buried by a chunk ranked
#: second by both, which is the behaviour we want when fusing incomparable scores.
RRF_K = 60


def tokenize(value: str) -> list[str]:
    """Split mixed Chinese/English text into ordered tokens, keeping duplicates.

    English runs of two or more alphanumerics become one token; every CJK run is
    split into overlapping two-character tokens so short Chinese queries still
    match longer passages.
    """
    tokens = _WORD.findall(value.lower())
    for sequence in _CJK.findall(value):
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


def terms(value: str) -> set[str]:
    """The set of distinct tokens, used for exact subset comparisons."""
    return set(tokenize(value))


class Retriever(Protocol):
    """The surface the workflow, API, UI and MCP tools rely on."""

    name: str
    chunks: list[Chunk]

    def search(self, query: str, limit: int = 5, min_coverage: float = 0.15) -> list[Chunk]: ...

    def get(self, chunk_id: str) -> Chunk | None: ...


class BM25Index:
    """Okapi BM25 ranking over a tokenized chunk collection.

    Scores use Robertson/Sparck Jones IDF with Lucene's `+1` smoothing (so a term
    appearing in many chunks never scores negatively) and per-chunk length
    normalization through `b`.
    """

    name = "bm25"

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1 = k1
        self.b = b
        self._frequencies = [Counter(tokenize(chunk.text)) for chunk in chunks]
        self._lengths = [sum(counter.values()) for counter in self._frequencies]
        self._average_length = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0

        document_frequency: Counter[str] = Counter()
        for counter in self._frequencies:
            document_frequency.update(counter.keys())
        total = len(chunks)
        self._idf = {
            term: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(self, query: str, limit: int = 5, min_coverage: float = 0.15) -> list[Chunk]:
        """Rank chunks with BM25, dropping any that fail the relevance floor.

        A chunk is kept only when it shares at least two of the query's terms (for queries
        with three or more terms) and when it covers at least `min_coverage` of the IDF mass
        of the query terms that actually occur in the corpus. Tokens that occur nowhere in the
        corpus cannot be covered by any chunk, so leaving them in the denominator would make
        the floor depend on how long the question happens to be; they are excluded instead.
        Without this floor an unrelated question still matches a chunk through a single stray
        token, and the agent answers it instead of reporting insufficient evidence.
        """
        wanted = terms(query)
        if not wanted or not self.chunks:
            return []
        known = [term for term in wanted if term in self._idf]
        if not known:
            return []
        query_mass = sum(self._idf[term] for term in known)
        multiple_terms_required = len(wanted) >= 3
        average_length = self._average_length if self._average_length > 0 else 1.0
        scored: list[tuple[float, int]] = []
        for index, counter in enumerate(self._frequencies):
            matched = [term for term in known if counter.get(term)]
            if not matched or (multiple_terms_required and len(matched) < 2):
                continue
            matched_mass = sum(self._idf[term] for term in matched)
            if query_mass and matched_mass / query_mass < min_coverage:
                continue
            length = self._lengths[index]
            score = 0.0
            for term in wanted:
                frequency = counter.get(term, 0)
                if not frequency:
                    continue
                normalization = 1 - self.b + self.b * length / average_length
                score += self._idf[term] * (frequency * (self.k1 + 1)) / (frequency + self.k1 * normalization)
            scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [self.chunks[index] for _, index in scored[:limit]]

    def get(self, chunk_id: str) -> Chunk | None:
        return next((chunk for chunk in self.chunks if chunk.id == chunk_id), None)


def reciprocal_rank_fusion(rankings: Iterable[list[str]], limit: int, k: int = RRF_K) -> list[str]:
    """Fuse several ranked id lists into one, scoring each id by the sum of ``1/(k+rank)``.

    RRF is used instead of adding the raw scores because BM25 scores and cosine
    similarities live on incomparable scales; converting both to ranks is what makes
    the combination honest rather than a tunable weighted sum. Ties break on the order
    in which an id was first seen, which keeps the result stable across runs.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
            first_seen.setdefault(chunk_id, len(first_seen))
    ordered = sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
    return [chunk_id for chunk_id, _ in ordered[:limit]]


class VectorIndex:
    """Dense ranking over the same chunks, using any :class:`~citeguard.embeddings.Embedder`.

    The chunk vectors are computed once in the constructor so query time is a single
    embedding call plus one sparse dot product per chunk.
    """

    name = "vector"

    def __init__(self, chunks: list[Chunk], embedder: Embedder, min_similarity: float = 0.0):
        self.chunks = chunks
        self.embedder = embedder
        self.min_similarity = min_similarity
        self._vectors = embedder.embed([chunk.text for chunk in chunks]) if chunks else []

    @property
    def dim(self) -> int:
        return self.embedder.dim

    def search(self, query: str, limit: int = 5, min_coverage: float = 0.15) -> list[Chunk]:
        """Rank chunks by cosine similarity, ignoring shapes the embedder cannot score.

        ``min_coverage`` is accepted and ignored: it belongs to the lexical channel. The
        dense channel has no defensible way to decide that a question is unanswerable —
        a hashed or embedding vector is close to *something* in every corpus — so that
        judgement is left to :class:`BM25Index` and to :class:`HybridIndex`.
        """
        if not self.chunks or not query.strip():
            return []
        query_vector = self.embedder.embed([query])[0]
        if not query_vector:
            return []
        scored = sorted(
            ((cosine(query_vector, vector), index) for index, vector in enumerate(self._vectors)),
            key=lambda item: (-item[0], item[1]),
        )
        return [
            self.chunks[index]
            for score, index in scored[:limit]
            if score > self.min_similarity
        ]

    def get(self, chunk_id: str) -> Chunk | None:
        return next((chunk for chunk in self.chunks if chunk.id == chunk_id), None)


class HybridIndex:
    """BM25 fused with a dense channel, with the lexical floor still deciding refusals.

    The design keeps the property the offline evaluation depends on: the BM25 relevance
    floor decides *whether* the corpus can answer at all, so an unrelated question still
    returns nothing and the agent still reports insufficient evidence. The dense channel
    only re-ranks the candidate pool the floor produced, which changes which five chunks
    reach the model without letting an unrelated chunk in through a similarity score.

    Setting ``admit_dense_only`` lets the dense channel add chunks the floor rejected.
    That widens recall on paraphrased questions but gives up the floor's precision
    guarantee, so it is off by default and measured as a separate ablation in
    ``evals/run_eval.py`` rather than silently enabled.
    """

    name = "hybrid"

    def __init__(
        self,
        chunks: list[Chunk],
        embedder: Embedder,
        min_coverage: float = 0.15,
        candidate_factor: int = 3,
        admit_dense_only: bool = False,
        k: int = RRF_K,
    ):
        self.chunks = chunks
        self.embedder = embedder
        self.min_coverage = min_coverage
        self.candidate_factor = max(1, int(candidate_factor))
        self.admit_dense_only = admit_dense_only
        self.k = k
        self._lexical = BM25Index(chunks)
        self._dense = VectorIndex(chunks, embedder)
        self._by_id = {chunk.id: chunk for chunk in chunks}

    def search(self, query: str, limit: int = 5, min_coverage: float | None = None) -> list[Chunk]:
        floor = self.min_coverage if min_coverage is None else min_coverage
        depth = max(limit, limit * self.candidate_factor)
        lexical = self._lexical.search(query, limit=depth, min_coverage=floor)
        if not lexical:
            return []
        dense = self._dense.search(query, limit=depth)
        if not dense:
            return lexical[:limit]
        lexical_ids = [chunk.id for chunk in lexical]
        if self.admit_dense_only:
            candidates = [lexical_ids, [chunk.id for chunk in dense]]
        else:
            allowed = set(lexical_ids)
            candidates = [lexical_ids, [chunk.id for chunk in dense if chunk.id in allowed]]
        fused = reciprocal_rank_fusion(candidates, limit, self.k)
        return [self._by_id[chunk_id] for chunk_id in fused if chunk_id in self._by_id]

    def get(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)


def build_index(chunks: list[Chunk], settings: Settings | None = None) -> Retriever:
    """Build the configured retriever.

    ``CITEGUARD_RETRIEVER`` selects the strategy:

    ``lexical``
        BM25 only.
    ``hybrid``
        Fuse BM25 with the configured dense encoder.
    ``auto`` (default)
        Hybrid when a real embedding endpoint is configured, otherwise lexical.

    The ``auto`` rule is a measurement, not a preference. The bundled offline encoder is
    a *lexical* hashing encoder, so fusing it into an already lexical ranking adds no
    information; `evals/run_eval.py` records the ablation showing it displaces the right
    chunk in one cross-document case (accuracy 0.9583 against 1.0 for BM25 alone). A real
    embedding endpoint is the only configuration where the second channel carries
    something the first one does not, so that is where it is switched on by default.
    """
    settings = settings or Settings.from_env()
    mode = settings.retriever
    if mode == "lexical":
        return BM25Index(chunks)
    embedder = build_embedder(settings)
    if embedder is None:
        return BM25Index(chunks)
    if mode == "auto" and settings.embedding_provider != "http":
        return BM25Index(chunks)
    return HybridIndex(chunks, embedder, min_coverage=settings.retrieval_min_coverage)
