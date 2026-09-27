"""Dense vector encoders for CiteGuard's hybrid retrieval.

Two encoders share one interface, so the retrieval code never knows which one is
attached:

``HashingEmbedder``
    Deterministic feature hashing over word tokens and character n-grams. It runs
    offline with no key and no model download, which is what lets the offline
    evaluations and CI exercise the fusion path. It is a *lexical* encoder: it
    captures character overlap, not synonyms.

``HttpEmbedder``
    Calls any OpenAI-compatible ``/embeddings`` endpoint. DeepSeek serves chat
    completions but no embedding model, so this exists to let a real embedding
    provider (SiliconFlow, DashScope, OpenAI, Ollama, vLLM, ...) drive the dense
    channel; only then does that channel carry genuine semantic recall.

Vectors are stored as sparse ``{index: weight}`` dictionaries and L2-normalized, so
cosine similarity costs one pass over the non-zero entries and the same code serves
both encoders.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

import httpx

from .config import DEFAULT_EMBEDDING_DIM, Settings

_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN_RUN = re.compile(r"[a-z0-9]+")
DEFAULT_DIM = DEFAULT_EMBEDDING_DIM
_CJK_NGRAM_SIZES = (2, 3, 4)
_LATIN_NGRAM_SIZE = 4

SparseVector = dict[int, float]


class Embedder(Protocol):
    """Anything that can turn text into unit-length sparse vectors."""

    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[SparseVector]: ...


def _features(text: str) -> list[str]:
    """The features one vector is hashed from.

    Word tokens (with CJK bigrams) come from ``retrieval.tokenize`` so both channels
    see the same segmentation; character n-grams are added on top so near-miss
    spellings and unlisted compound words still share weight.
    """
    from .retrieval import tokenize  # local import: retrieval imports this module

    features = list(tokenize(text))
    for run in _CJK_RUN.findall(text):
        features.extend(run)
        for size in _CJK_NGRAM_SIZES:
            features.extend(run[index : index + size] for index in range(len(run) - size + 1))
    for run in _LATIN_RUN.findall(text.lower()):
        if len(run) >= _LATIN_NGRAM_SIZE:
            features.extend(
                run[index : index + _LATIN_NGRAM_SIZE]
                for index in range(len(run) - _LATIN_NGRAM_SIZE + 1)
            )
    return features


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    """Map a feature to a bucket and a sign.

    ``hashlib`` is used instead of the built-in ``hash`` because the latter is salted
    per process, which would make the index non-reproducible across runs. The sign bit
    keeps colliding features from always adding up.
    """
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if value >> 63 else -1.0


def _normalize(vector: SparseVector) -> SparseVector:
    norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    if norm == 0.0:
        return {}
    return {index: weight / norm for index, weight in vector.items()}


def cosine(left: SparseVector, right: SparseVector) -> float:
    """Cosine similarity of two unit-length sparse vectors."""
    if len(right) < len(left):
        left, right = right, left
    return sum(weight * right.get(index, 0.0) for index, weight in left.items())


class HashingEmbedder:
    """Offline encoder: sublinear term frequency over hashed token and n-gram features.

    No IDF weighting on purpose. BM25 already supplies the rarity-weighted view of the
    corpus, and the point of a second channel is to rank differently, not to repeat the
    first one.
    """

    name = "hashing"

    def __init__(self, dim: int = DEFAULT_DIM):
        self.dim = max(32, int(dim))

    def embed(self, texts: list[str]) -> list[SparseVector]:
        vectors: list[SparseVector] = []
        for text in texts:
            counts = Counter(_features(text))
            vector: SparseVector = {}
            for feature, count in counts.items():
                index, sign = _bucket(feature, self.dim)
                vector[index] = vector.get(index, 0.0) + sign * (1.0 + math.log(count))
            vectors.append(_normalize(vector))
        return vectors


class HttpEmbedder:
    """Client for an OpenAI-compatible ``POST {base_url}/embeddings`` endpoint."""

    name = "http"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        dim: int = 0,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = int(dim)
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[SparseVector]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json={"model": self.model, "input": list(texts)},
            timeout=self.timeout,
        )
        response.raise_for_status()
        # The response is ordered by `index` per the OpenAI schema; sort anyway so a
        # provider that returns batches out of order cannot silently misalign chunks.
        items = sorted(response.json().get("data") or [], key=lambda item: item.get("index", 0))
        if len(items) != len(texts):
            raise ValueError(
                f"the embedding endpoint returned {len(items)} vectors for {len(texts)} inputs"
            )
        vectors: list[SparseVector] = []
        for item in items:
            embedding = item.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise ValueError("the embedding endpoint returned an empty vector")
            self.dim = self.dim or len(embedding)
            vectors.append(_normalize({index: float(value) for index, value in enumerate(embedding) if value}))
        return vectors


def build_embedder(settings: Settings | None = None) -> Embedder | None:
    """Build the configured encoder, or ``None`` when the dense channel is switched off.

    ``CITEGUARD_EMBEDDING_PROVIDER`` selects ``hashing`` (default), ``http`` or ``off``.
    """
    settings = settings or Settings.from_env()
    provider = settings.embedding_provider
    if provider == "off":
        return None
    if provider == "http":
        if not settings.embedding_base_url or not settings.embedding_model:
            raise ValueError(
                "CITEGUARD_EMBEDDING_PROVIDER=http needs CITEGUARD_EMBEDDING_BASE_URL and "
                "CITEGUARD_EMBEDDING_MODEL; set CITEGUARD_EMBEDDING_API_KEY as well when the "
                "endpoint requires one"
            )
        return HttpEmbedder(
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            dim=settings.embedding_dim,
        )
    return HashingEmbedder(settings.embedding_dim)
