"""MCP server exposing DataPilot's safe analysis tools over stdio.

Every tool is a read-only adapter over the dataset the HTTP API serves (see
``state.py``). ``run_safe_query`` goes through the same SQL policy gate and the same
in-memory SQLite engine as the agent, so a query that is refused by the agent is
refused here too and comes back as ``policy_blocked`` rather than as an exception.

Run it as a standalone stdio server::

    python -m datapilot.mcp_server
"""

from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import state
from .execution import execute_sql
from .models import QueryResult
from .profiling import profile_dataset
from .workflow import choose_chart

SERVER_NAME = "datapilot"


def _package_version() -> str:
    """Report the version of this package, not the version of the mcp SDK."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("datapilot")
    except (ImportError, PackageNotFoundError):  # pragma: no cover - source checkout
        return "0.0.0"


SCHEMA_TOOL = types.Tool(
    name="get_schema",
    description=(
        "Describe the dataset currently loaded by the server: table name, row count and, "
        "for every column, its inferred type, null count and example values."
    ),
    inputSchema={"type": "object", "properties": {}},
)

QUERY_TOOL = types.Tool(
    name="run_safe_query",
    description=(
        "Run one read-only SELECT statement against the loaded dataset and return the "
        "columns, rows and row count. Statements that are not read-only SELECTs are "
        "refused by the SQL policy gate and return policy_blocked=true."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single read-only SELECT statement."}
        },
        "required": ["sql"],
    },
)

CHART_TOOL = types.Tool(
    name="make_chart_spec",
    description=(
        "Ask for the chart the agent would draw for a result shape. Returns kind=table when "
        "the shape does not support a chart, matching what the agent itself would render."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}, "description": "Column names."},
            "rows": {"type": "array", "description": "Rows, each a list of values."},
        },
        "required": ["columns", "rows"],
    },
)

server = Server(SERVER_NAME, version=_package_version())


def get_schema() -> dict:
    """Return the profile of the loaded dataset as a JSON-safe dictionary."""
    return profile_dataset(state.get_handle()).model_dump()


def run_safe_query(sql: str) -> dict:
    """Execute one gated read-only query and return the result as a JSON-safe dictionary."""
    return execute_sql(state.get_handle(), sql).model_dump()


def make_chart_spec(columns: list[str], rows: list[list[object]]) -> dict:
    """Return the chart the agent would draw for this shape.

    Reuses ``workflow.choose_chart`` so a client cannot obtain a chart the agent itself
    would have refused to draw. Falls back to the ``table`` kind, the same default as
    ``ChartSpec``.
    """
    result = QueryResult(columns=[str(column) for column in columns], rows=[list(row) for row in rows], row_count=len(rows))
    spec = choose_chart(result)
    return spec.model_dump() if spec is not None else {"kind": "table", "x": None, "y": None}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [SCHEMA_TOOL, QUERY_TOOL, CHART_TOOL]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "get_schema":
        return {"schema": get_schema()}
    if name == "run_safe_query":
        return {"sql": str(arguments["sql"]), "result": run_safe_query(str(arguments["sql"]))}
    if name == "make_chart_spec":
        columns = [str(column) for column in arguments["columns"]]
        rows = [list(row) for row in arguments["rows"]]
        return {"chart": make_chart_spec(columns, rows)}
    raise ValueError(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
