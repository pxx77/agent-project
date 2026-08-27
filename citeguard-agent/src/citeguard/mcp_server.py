from .api import _index


def search_documents(query: str, limit: int = 5) -> list[dict]:
    return [chunk.model_dump() for chunk in _index.search(query, limit)]


def get_chunk(chunk_id: str) -> dict | None:
    chunk = _index.get(chunk_id)
    return chunk.model_dump() if chunk else None
