"""Protocol-level tests for the CiteGuard MCP server.

The tests drive the real server through ``create_connected_server_and_client_session``
rather than calling the tool functions directly, so what is asserted here is what an MCP
client actually observes: the advertised schemas, the JSON structured content, and how
invalid input is reported.
"""

import asyncio
import sys
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from citeguard import state
from citeguard.mcp_server import DEFAULT_LIMIT, MAX_LIMIT, server
from citeguard.models import Chunk
from citeguard.retrieval import BM25Index


def _run(coro):
    """Drive the async protocol from a sync test, so no async test plugin is required."""
    return asyncio.run(coro)


async def _list_tools():
    async with create_connected_server_and_client_session(server) as client:
        return await client.list_tools()


async def _call(name: str, arguments: dict[str, Any]):
    async with create_connected_server_and_client_session(server) as client:
        return await client.call_tool(name, arguments)


async def _tools_over_stdio():
    """Connect the way a real MCP client does, by spawning the documented command."""
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "citeguard.mcp_server"])
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.list_tools(), await session.call_tool("search_documents", {"query": "citation support"})


def _chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(id=chunk_id, document_id="doc-1", text=text, start=0, end=len(text))


def test_server_advertises_the_retrieval_tools():
    result = _run(_list_tools())
    tools = {tool.name: tool for tool in result.tools}

    assert set(tools) == {"search_documents", "get_chunk"}
    assert tools["search_documents"].inputSchema["required"] == ["query"]
    assert tools["search_documents"].inputSchema["properties"]["limit"]["maximum"] == MAX_LIMIT
    assert tools["get_chunk"].inputSchema["required"] == ["chunk_id"]


def test_tool_descriptions_say_what_they_return():
    """A client picks tools from these strings, so they have to name the real behaviour."""
    result = _run(_list_tools())
    described = {tool.name: tool.description for tool in result.tools}

    assert "chunks" in described["search_documents"]
    assert "get_chunk" in described["search_documents"]
    assert "id" in described["get_chunk"]


def test_default_index_serves_the_bundled_fixture():
    """The server is usable with no setup: it reads the same index ``state`` exposes."""
    result = _run(_call("search_documents", {"query": "citation support"}))

    assert result.isError is False
    assert result.structuredContent["query"] == "citation support"
    assert result.structuredContent["chunks"]


def test_search_documents_returns_the_best_match_first(monkeypatch):
    index = BM25Index(
        [
            _chunk("c0", "Orchid protocol requires human approval for payments."),
            _chunk("c1", "Citation support measures whether every claim is backed by evidence."),
            _chunk("c2", "The handbook covers latency, cost and bounded retries."),
        ]
    )
    monkeypatch.setattr(state, "get_index", lambda: index)

    result = _run(_call("search_documents", {"query": "orchid approval"}))

    assert [chunk["id"] for chunk in result.structuredContent["chunks"]] == ["c0"]


def test_search_documents_returns_no_chunks_for_an_unrelated_query(monkeypatch):
    """The relevance floor is what stops the agent answering an unrelated question."""
    index = BM25Index([_chunk("c0", "The handbook covers latency and cost.")])
    monkeypatch.setattr(state, "get_index", lambda: index)

    result = _run(_call("search_documents", {"query": "orbital mechanics"}))

    assert result.structuredContent["chunks"] == []


def test_search_documents_defaults_to_the_declared_limit_and_honours_a_smaller_one(monkeypatch):
    index = BM25Index([_chunk(f"c{index}", f"citation support policy revision {index}.") for index in range(7)])
    monkeypatch.setattr(state, "get_index", lambda: index)

    default = _run(_call("search_documents", {"query": "citation support"}))
    limited = _run(_call("search_documents", {"query": "citation support", "limit": 2}))

    assert len(default.structuredContent["chunks"]) == DEFAULT_LIMIT
    assert len(limited.structuredContent["chunks"]) == 2


def test_get_chunk_reads_back_a_chunk_returned_by_search(monkeypatch):
    index = BM25Index([_chunk("c0", "Orchid protocol requires human approval for payments.")])
    monkeypatch.setattr(state, "get_index", lambda: index)

    found = _run(_call("search_documents", {"query": "orchid protocol"}))
    chunk_id = found.structuredContent["chunks"][0]["id"]
    read = _run(_call("get_chunk", {"chunk_id": chunk_id}))

    assert read.isError is False
    assert read.structuredContent["found"] is True
    assert read.structuredContent["chunk"]["id"] == chunk_id
    assert read.structuredContent["chunk"]["document_id"] == "doc-1"


def test_get_chunk_reports_an_unknown_id_as_data_not_as_a_failure():
    result = _run(_call("get_chunk", {"chunk_id": "does-not-exist"}))

    assert result.isError is False
    assert result.structuredContent == {"chunk_id": "does-not-exist", "found": False, "chunk": None}


def test_declared_limit_bounds_are_enforced_before_the_tool_runs():
    for limit in (0, MAX_LIMIT + 1):
        result = _run(_call("search_documents", {"query": "citation", "limit": limit}))

        assert result.isError is True
        assert "Input validation error" in result.content[0].text


def test_a_missing_required_argument_is_rejected():
    result = _run(_call("search_documents", {}))

    assert result.isError is True
    assert "Input validation error" in result.content[0].text


def test_an_unknown_tool_name_is_reported_as_an_error():
    result = _run(_call("verify_claim", {"claim": "anything"}))

    assert result.isError is True
    assert "Unknown tool" in result.content[0].text


def test_the_documented_launch_command_serves_the_same_tools():
    """``python -m citeguard.mcp_server`` is what the README tells clients to run."""
    listed, searched = _run(_tools_over_stdio())

    assert {tool.name for tool in listed.tools} == {"search_documents", "get_chunk"}
    assert searched.isError is False
    assert searched.structuredContent["chunks"]
