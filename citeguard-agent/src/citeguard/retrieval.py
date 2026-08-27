from __future__ import annotations

import re

from .models import Chunk


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", value.lower()))


class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._terms = [_terms(chunk.text) for chunk in chunks]

    def search(self, query: str, limit: int = 5) -> list[Chunk]:
        wanted = _terms(query)
        if not wanted:
            return []
        ranked = sorted(((len(wanted & terms), -idx, chunk) for idx, (terms, chunk) in enumerate(zip(self._terms, self.chunks))), reverse=True)
        return [chunk for score, _, chunk in ranked[:limit] if score > 0]

    def get(self, chunk_id: str) -> Chunk | None:
        return next((chunk for chunk in self.chunks if chunk.id == chunk_id), None)
