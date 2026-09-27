"""Protocol-level tests for the DataPilot MCP server.

The tests drive the real server through ``create_connected_server_and_client_session``
rather than calling the tool functions directly, so what is asserted here is what an MCP
client actually observes: the advertised schemas, the JSON structured content, and how a
refused statement is reported.
"""

import asyncio
import sys
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from datapilot import state
from datapilot.mcp_server import server
from datapilot.models import DatasetHandle, QueryResult
from datapilot.sql_policy import validate_sql
from datapilot.workflow import choose_chart

READ_ONLY_SQL = "SELECT region, SUM(revenue) AS total FROM sales GROUP BY region ORDER BY total DESC"


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
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "datapilot.mcp_server"])
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.list_tools(), await session.call_tool("run_safe_query", {"sql": READ_ONLY_SQL})


def test_server_advertises_the_analysis_tools():
    result = _run(_list_tools())
    tools = {tool.name: tool for tool in result.tools}

    assert set(tools) == {"get_schema", "run_safe_query", "make_chart_spec"}
    assert tools["run_safe_query"].inputSchema["required"] == ["sql"]
    assert tools["make_chart_spec"].inputSchema["required"] == ["columns", "rows"]
    assert tools["get_schema"].inputSchema["properties"] == {}


def test_get_schema_describes_the_loaded_dataset():
    result = _run(_call("get_schema", {}))

    assert result.isError is False
    table = result.structuredContent["schema"]["tables"]["sales"]

    assert table["row_count"] == 12
    assert [column["name"] for column in table["columns"]] == ["region", "revenue", "orders"]
    assert {column["type"] for column in table["columns"]} == {"string", "number"}


def test_run_safe_query_returns_rows_for_a_select():
    result = _run(_call("run_safe_query", {"sql": READ_ONLY_SQL}))

    assert result.isError is False
    query = result.structuredContent["result"]

    assert query["columns"] == ["region", "total"]
    assert query["row_count"] == 4
    assert query["error"] is None
    assert query["policy_blocked"] is False


def test_run_safe_query_returns_policy_blocked_as_data_rather_than_raising():
    """This is the promise the module docstring makes: a refused statement is a result,
    not a transport error, so a client can tell it apart from a broken server."""
    result = _run(_call("run_safe_query", {"sql": "DROP TABLE sales"}))

    assert result.isError is False
    query = result.structuredContent["result"]

    assert query["policy_blocked"] is True
    assert query["error"] == "only one read-only SELECT statement is allowed"
    assert query["rows"] == []
    assert result.structuredContent["sql"] == "DROP TABLE sales"


def test_the_policy_gate_is_the_same_one_the_agent_uses():
    """``run_safe_query`` must not be a laxer path into the dataset than the agent's."""
    for sql in ("", "DROP TABLE sales", "SELECT 1; SELECT 2", "SELECT * FROM read_csv('x')", READ_ONLY_SQL):
        result = _run(_call("run_safe_query", {"sql": sql}))
        decision = validate_sql(sql)

        assert result.structuredContent["result"]["policy_blocked"] is (not decision.allowed)
        assert result.structuredContent["result"]["error"] == (None if decision.allowed else decision.reason)


def test_a_broken_select_is_reported_as_a_query_error_not_as_policy_blocked():
    result = _run(_call("run_safe_query", {"sql": "SELECT missing_column FROM sales"}))

    query = result.structuredContent["result"]

    assert result.isError is False
    assert query["policy_blocked"] is False
    assert "missing_column" in query["error"]


def test_make_chart_spec_reuses_the_agents_chart_heuristic():
    result = _run(_call("make_chart_spec", {"columns": ["region", "total"], "rows": [["East", 2800], ["West", 3200]]}))

    assert result.isError is False
    assert result.structuredContent["chart"] == {"kind": "bar", "x": "region", "y": "total"}


def test_make_chart_spec_falls_back_to_table_when_the_agent_would_not_chart():
    shape = QueryResult(columns=["region"], rows=[["East"]], row_count=1)

    assert choose_chart(shape) is None
    result = _run(_call("make_chart_spec", {"columns": ["region"], "rows": [["East"]]}))

    assert result.structuredContent["chart"] == {"kind": "table", "x": None, "y": None}


def test_a_query_result_can_be_fed_straight_back_into_make_chart_spec():
    """A client that runs a query then asks for a chart must not have to reshape the rows."""
    query = _run(_call("run_safe_query", {"sql": READ_ONLY_SQL})).structuredContent["result"]
    chart = _run(_call("make_chart_spec", {"columns": query["columns"], "rows": query["rows"]}))

    assert chart.structuredContent["chart"]["kind"] == "bar"
    assert chart.structuredContent["chart"]["y"] == "total"


def test_the_uploaded_dataset_replaces_the_one_the_tools_read(monkeypatch):
    """The tools follow ``state``, the same holder the HTTP upload path writes to."""
    monkeypatch.setattr(state, "get_handle", lambda: DatasetHandle("campaign", [{"segment": "A", "amount": "10"}]))

    schema = _run(_call("get_schema", {}))
    query = _run(_call("run_safe_query", {"sql": "SELECT segment, amount FROM campaign"}))

    assert "campaign" in schema.structuredContent["schema"]["tables"]
    assert query.structuredContent["result"]["row_count"] == 1


def test_a_missing_required_argument_is_rejected():
    result = _run(_call("run_safe_query", {}))

    assert result.isError is True
    assert "Input validation error" in result.content[0].text


def test_an_unknown_tool_name_is_reported_as_an_error():
    result = _run(_call("export_dataset", {"format": "csv"}))

    assert result.isError is True
    assert "Unknown tool" in result.content[0].text


def test_the_documented_launch_command_serves_the_same_tools():
    """``python -m datapilot.mcp_server`` is what the README tells clients to run."""
    listed, queried = _run(_tools_over_stdio())

    assert {tool.name for tool in listed.tools} == {"get_schema", "run_safe_query", "make_chart_spec"}
    assert queried.isError is False
    assert queried.structuredContent["result"]["row_count"] == 4
