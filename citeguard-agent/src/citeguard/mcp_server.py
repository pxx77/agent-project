"""MCP server exposing CiteGuard's retrieval tools over stdio.

The tools are read-only adapters over the same index the HTTP API serves (see
``state.py``), so they offer nothing the API does not already have: retrieval
returns raw chunks and never invents an answer. Claim verification stays inside
the agent, which is why no tool here returns a verdict.

Run it as a standalone stdio server::

    python -m citeguard.mcp_server

or let an MCP client launch it with ``python -m citeguard.mcp_server``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import state

DEFAULT_LIMIT = 5
MAX_LIMIT = 50
SERVER_NAME = "citeguard"


def _package_version() -> str:
    """Report the version of this package, not the version of the mcp SDK."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("citeguard")
    except (ImportError, PackageNotFoundError):  # pragma: no cover - source checkout
        return "0.0.0"


SEARCH_TOOL = types.Tool(
    name="search_documents",
    description=(
        "Search the indexed documents and return the most relevant chunks with their ids. "
        "Pass a returned id to get_chunk to read that chunk in full."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query."},
            "limit": {
                "type": "integer",
                "description": f"Maximum number of chunks to return (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "default": DEFAULT_LIMIT,
            },
        },
        "required": ["query"],
    },
)

CHUNK_TOOL = types.Tool(
    name="get_chunk",
    description="Read one indexed chunk by the id returned from search_documents.",
    inputSchema={
        "type": "object",
        "properties": {"chunk_id": {"type": "string", "description": "Id of the chunk to read."}},
        "required": ["chunk_id"],
    },
)

server = Server(SERVER_NAME, version=_package_version())


def search_documents(query: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Return the matching chunks as JSON-safe dictionaries, best match first."""
    return [chunk.model_dump() for chunk in state.get_index().search(query, limit)]


def get_chunk(chunk_id: str) -> dict | None:
    """Return one chunk as a JSON-safe dictionary, or ``None`` when it does not exist."""
    chunk = state.get_index().get(chunk_id)
    return chunk.model_dump() if chunk else None


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [SEARCH_TOOL, CHUNK_TOOL]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "search_documents":
        query = str(arguments["query"])
        # The declared schema already enforces 1 <= limit <= MAX_LIMIT.
        limit = int(arguments.get("limit", DEFAULT_LIMIT))
        return {"query": query, "chunks": search_documents(query, limit)}
    if name == "get_chunk":
        chunk_id = str(arguments["chunk_id"])
        chunk = get_chunk(chunk_id)
        # An unknown id is reported as data rather than an exception so the client
        # can tell "no such chunk" apart from "the server failed".
        return {"chunk_id": chunk_id, "found": chunk is not None, "chunk": chunk}
    raise ValueError(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
