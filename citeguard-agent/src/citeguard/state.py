"""Process-wide state shared by the HTTP API and the MCP server.

Both transports read the same index, so a document ingested through ``POST /ingest``
is visible to the MCP tools running in the same process. Two separate processes each
start from the bundled fixture corpus.
"""

from __future__ import annotations

from .retrieval import Retriever
from .workflow import build_fixture_agent

_index: Retriever = build_fixture_agent().index


def get_index() -> Retriever:
    """Return the index every transport in this process reads from."""
    return _index


def set_index(index: Retriever) -> None:
    """Replace the active index, for example after a document upload."""
    global _index
    _index = index
