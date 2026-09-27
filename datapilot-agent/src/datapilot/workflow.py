from __future__ import annotations

import json
import re
import time

from .config import Settings
from .execution import execute_sql
from .llm import build_model_client
from .models import AgentResult, ChartSpec, DatasetHandle, Profile, QueryResult, TableProfile, Trace, Verification
from .profiling import profile_dataset

_TEMPORAL = re.compile(r"date|month|year|day|time|quarter|week", re.I)
_TEMPORAL_VALUE = re.compile(r"^\d{4}[-/]\d{1,2}")


def _plan_context(handle: DatasetHandle, table_profile: TableProfile) -> dict:
    return {
        "instruction": "Generate one safe read-only SELECT statement and return JSON with a sql field.",
        "table": handle.name,
        "columns": [column.model_dump() for column in table_profile.columns],
    }


def _repair_context(
    handle: DatasetHandle,
    table_profile: TableProfile,
    question: str,
    failed_sql: str,
    error: str,
) -> dict:
    return {
        "instruction": (
            "The previous read-only query failed on the given table. Return JSON with a sql field "
            "containing a corrected single read-only SELECT statement that answers the same question. "
            "Only reference columns that exist in the schema."
        ),
        "table": handle.name,
        "columns": [column.model_dump() for column in table_profile.columns],
        "question": question,
        "failed_sql": failed_sql,
        "error": error,
    }


def _extract_sql(payload: dict) -> str | None:
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return None
    return sql.strip()


def _is_numeric(values: list[object]) -> bool:
    present = [value for value in values if value not in (None, "")]
    if not present:
        return False
    try:
        for value in present:
            float(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _looks_temporal(name: str, values: list[object]) -> bool:
    if _TEMPORAL.search(name):
        return True
    sample = [str(value) for value in values if value not in (None, "")]
    return bool(sample) and all(_TEMPORAL_VALUE.match(value) for value in sample[:5])


def choose_chart(result: QueryResult) -> ChartSpec | None:
    """Pick a chart only when the returned shape actually supports one. Public because
    the MCP tool ``make_chart_spec`` reuses it instead of keeping a second heuristic."""
    if result.error is not None or len(result.columns) < 2 or not result.rows:
        return None
    x_index, y_index = 0, len(result.columns) - 1
    x_values = [row[x_index] for row in result.rows]
    y_values = [row[y_index] for row in result.rows]
    if _is_numeric(x_values) or not _is_numeric(y_values):
        return None
    kind = "line" if _looks_temporal(str(result.columns[x_index]), x_values) else "bar"
    return ChartSpec(kind=kind, x=result.columns[x_index], y=result.columns[y_index])


def _conclusion(result: QueryResult, chart: ChartSpec | None) -> str:
    if result.error is not None:
        return f"Analysis failed: {result.error}"
    rows_label = "row" if result.row_count == 1 else "rows"
    columns_label = "column" if len(result.columns) == 1 else "columns"
    summary = f"Query returned {result.row_count} {rows_label} across {len(result.columns)} {columns_label}."
    if chart is None or not result.rows:
        return summary
    try:
        best = max(result.rows, key=lambda row: float(row[-1]))
    except (TypeError, ValueError):
        return summary
    return f"{summary} The largest {chart.y} is {best[-1]} for {best[0]}."


def run_agent(question: str, handle: DatasetHandle, client=None) -> AgentResult:
    started = time.perf_counter()
    settings = Settings.from_env()
    client = client or build_model_client(settings)
    trace = Trace(
        states=["profile", "plan", "policy", "execute"],
        provider=client.provider,
        model=client.model,
    )
    profile: Profile = profile_dataset(handle)
    table_profile = profile.tables[handle.name]

    payload = client.complete_json(
        json.dumps(_plan_context(handle, table_profile), ensure_ascii=False),
        question,
        "analysis",
    )
    trace.model_calls = 1
    sql = _extract_sql(payload)
    if sql is None:
        raise ValueError("Model response is missing a non-empty 'sql' field")

    result = execute_sql(handle, sql, settings.max_rows)
    while result.error and not result.policy_blocked and trace.retries < settings.max_retries:
        trace.retries += 1
        trace.states.append("repair")
        repair_payload = client.complete_json(
            json.dumps(_repair_context(handle, table_profile, question, sql, result.error or ""), ensure_ascii=False),
            question,
            "sql_repair",
        )
        trace.model_calls += 1
        candidate = _extract_sql(repair_payload)
        if candidate is None or candidate == sql:
            break
        sql = candidate
        result = execute_sql(handle, sql, settings.max_rows)

    trace.states.extend(["visualize", "verify"])
    chart = choose_chart(result)
    consistent = result.error is None and bool(result.columns)
    trace.status = "success" if consistent else "failed"
    trace.duration_ms = (time.perf_counter() - started) * 1000
    return AgentResult(
        sql=sql,
        query_result=result,
        chart=chart,
        conclusion=_conclusion(result, chart),
        verification=Verification(
            consistent=consistent,
            reason=(
                f"SQL executed read-only and returned {result.row_count} rows."
                if consistent
                else f"SQL did not produce a verified result: {result.error}"
            ),
        ),
        trace=trace,
    )


def build_fixture_agent(name: str = "sales.csv"):
    """Build an agent over a bundled fixture dataset.

    ``evals/run_eval.py`` passes other file names so a single run covers several schemas
    instead of only ``sales.csv``.
    """
    from .ingestion import load_fixture
    return FixtureAgent(load_fixture(name))


class FixtureAgent:
    def __init__(self, handle: DatasetHandle):
        self.handle = handle

    @property
    def name(self) -> str:
        return self.handle.name

    def run(self, question: str, client=None) -> AgentResult:
        return run_agent(question, self.handle, client)
