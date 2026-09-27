"""Process-wide state shared by the HTTP API and the MCP server.

Both transports read the same dataset handle, so a file uploaded through
``POST /profile`` is visible to the MCP tools running in the same process. Two
separate processes each start from the bundled fixture dataset.
"""

from __future__ import annotations

from .ingestion import load_fixture
from .models import DatasetHandle

_handle: DatasetHandle = load_fixture()


def get_handle() -> DatasetHandle:
    """Return the dataset every transport in this process reads from."""
    return _handle


def set_handle(handle: DatasetHandle) -> None:
    """Replace the active dataset, for example after a file upload."""
    global _handle
    _handle = handle
