from __future__ import annotations

import math
import re
from collections import Counter

from .models import Chunk

_WORD = re.compile(r"[a-z0-9]{2,}")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


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


class BM25Index:
    """Okapi BM25 ranking over a tokenized chunk collection.

    Scores use Robertson/Sparck Jones IDF with Lucene's `+1` smoothing (so a term
    appearing in many chunks never scores negatively) and per-chunk length
    normalization through `b`.
    """

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
