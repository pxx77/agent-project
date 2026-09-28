"""The DataPilot agent, expressed as a LangGraph state machine.

The pipeline is a bounded loop rather than a straight line: plan, check the SQL against policy,
execute it, and — when the database rejects it for a reason that is not a policy violation — feed
the error back to the model and try again, up to ``Settings.max_retries``. LangGraph models that
shape directly: nodes are the stages, ``add_conditional_edges`` is the branch, and the cycle
``execute -> repair -> execute`` is the retry loop.

``trace.states`` records the nodes the graph actually visited, in order, so a repaired run shows
``["profile", "plan", "policy", "execute", "repair", "execute", "visualize", "verify"]`` and a
policy-blocked run never reaches ``execute`` at all.
"""

from __future__ import annotations

import json
import operator
import re
import time
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .execution import execute_sql
from .llm import ModelClient, build_model_client
from .models import (
    AgentResult,
    ChartSpec,
    DatasetHandle,
    PolicyDecision,
    Profile,
    QueryResult,
    TableProfile,
    TokenUsage,
    Trace,
    Verification,
)
from .profiling import profile_dataset
from .sql_policy import validate_sql
from .usage import cost_of, drain_usage

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


def _record_usage(trace: Trace, client: ModelClient | None, settings: Settings) -> None:
    """Attach the tokens this run consumed, and what they cost.

    The drain is per run rather than per client lifetime, because the evaluation reuses one client
    across every case: a lifetime counter would report the whole run's tokens on the first case and
    double-count from there. A repair retry makes two calls in one run, and both land here.

    Offline replay clients have no real model of their own, so their tokens are priced at the
    configured model: the figure answers "what would these tokens have cost" instead of inventing a
    rate for a client that was never billed. Nothing was billed, which is exactly why
    ``usage.measured`` stays False.
    """
    if client is None:
        return
    usage: TokenUsage = drain_usage(client)
    billing_model = client.model if client.provider == "deepseek" else settings.model
    amount = cost_of(usage, billing_model)
    trace.usage = usage
    trace.billing_model = billing_model
    trace.cost_cny = round(amount, 8) if amount is not None else None


class _RunState(TypedDict, total=False):
    """What flows along the graph's edges.

    ``states``, ``model_calls`` and ``retries`` declare reducers, so a node reports only its own
    contribution and the graph accumulates the run's totals. Every other key keeps LangGraph's
    default last-write-wins behaviour, which is what a node overwriting ``sql`` or ``result`` wants.
    """

    # Inputs, fixed for the whole run.
    question: str
    handle: DatasetHandle
    client: ModelClient
    settings: Settings
    # Working values, rewritten as the run progresses.
    profile: Profile
    table_profile: TableProfile
    sql: str
    result: QueryResult
    allowed: bool
    stalled: bool
    chart: ChartSpec | None
    conclusion: str
    verification: Verification
    # Accumulators.
    states: Annotated[list[str], operator.add]
    model_calls: Annotated[int, operator.add]
    retries: Annotated[int, operator.add]


def _profile_node(state: _RunState) -> dict:
    handle = state["handle"]
    profile = profile_dataset(handle)
    return {
        "profile": profile,
        "table_profile": profile.tables[handle.name],
        "states": ["profile"],
    }


def _plan_node(state: _RunState) -> dict:
    handle = state["handle"]
    table_profile = state["table_profile"]
    payload = state["client"].complete_json(
        json.dumps(_plan_context(handle, table_profile), ensure_ascii=False),
        state["question"],
        "analysis",
    )
    sql = _extract_sql(payload)
    if sql is None:
        raise ValueError("Model response is missing a non-empty 'sql' field")
    return {"sql": sql, "states": ["plan"], "model_calls": 1}


def _policy_node(state: _RunState) -> dict:
    """Gate the SQL before anything touches the database.

    A rejection here is final: it is not a syntax problem the model can fix by trying again, so the
    blocked run routes straight past the retry loop. ``execute_sql`` re-checks the same policy, which
    keeps the MCP tool's standalone path honest too.
    """
    decision: PolicyDecision = validate_sql(state["sql"])
    if not decision.allowed:
        return {
            "allowed": False,
            "result": QueryResult(error=decision.reason, policy_blocked=True),
            "states": ["policy"],
        }
    return {"allowed": True, "states": ["policy"]}


def _execute_node(state: _RunState) -> dict:
    result = execute_sql(state["handle"], state["sql"], state["settings"].max_rows)
    return {"result": result, "states": ["execute"]}


def _repair_node(state: _RunState) -> dict:
    """Ask the model to correct a query the database rejected, and report whether it improved.

    ``stalled`` is what bounds the loop: when the model returns nothing usable, or hands back the
    same statement again, there is no point spending another call, so the next routing decision ends
    the cycle instead of re-entering it.
    """
    handle = state["handle"]
    table_profile = state["table_profile"]
    sql = state["sql"]
    payload = state["client"].complete_json(
        json.dumps(
            _repair_context(handle, table_profile, state["question"], sql, state["result"].error or ""),
            ensure_ascii=False,
        ),
        state["question"],
        "sql_repair",
    )
    candidate = _extract_sql(payload)
    if candidate is None or candidate == sql:
        return {"stalled": True, "states": ["repair"], "model_calls": 1, "retries": 1}
    return {"sql": candidate, "stalled": False, "states": ["repair"], "model_calls": 1, "retries": 1}


def _visualize_node(state: _RunState) -> dict:
    result = state["result"]
    chart = choose_chart(result)
    return {"chart": chart, "conclusion": _conclusion(result, chart), "states": ["visualize"]}


def _verify_node(state: _RunState) -> dict:
    result = state["result"]
    consistent = result.error is None and bool(result.columns)
    return {
        "verification": Verification(
            consistent=consistent,
            reason=(
                f"SQL executed read-only and returned {result.row_count} rows."
                if consistent
                else f"SQL did not produce a verified result: {result.error}"
            ),
        ),
        "states": ["verify"],
    }


def _route_after_policy(state: _RunState) -> str:
    return "execute" if state["allowed"] else "visualize"


def _route_after_execute(state: _RunState) -> str:
    """Decide whether this run still has retries to spend.

    Mirrors the exit condition of the original loop: only a genuine execution error is repairable, a
    policy block is not, and a repair that changed nothing sets ``stalled`` to stop the cycle.
    """
    result = state["result"]
    if (
        result.error is not None
        and not result.policy_blocked
        and not state.get("stalled", False)
        and state["retries"] < state["settings"].max_retries
    ):
        return "repair"
    return "visualize"


def build_graph():
    """Assemble the agent as a LangGraph ``StateGraph``.

    Kept as a factory (rather than only a module-level constant) so the topology can be rebuilt and
    inspected, e.g. by a test that renders it.
    """
    graph = StateGraph(_RunState)
    graph.add_node("profile", _profile_node)
    graph.add_node("plan", _plan_node)
    graph.add_node("policy", _policy_node)
    graph.add_node("execute", _execute_node)
    graph.add_node("repair", _repair_node)
    graph.add_node("visualize", _visualize_node)
    graph.add_node("verify", _verify_node)

    graph.add_edge(START, "profile")
    graph.add_edge("profile", "plan")
    graph.add_edge("plan", "policy")
    graph.add_conditional_edges("policy", _route_after_policy, ["execute", "visualize"])
    graph.add_conditional_edges("execute", _route_after_execute, ["repair", "visualize"])
    graph.add_edge("repair", "execute")
    graph.add_edge("visualize", "verify")
    graph.add_edge("verify", END)

    return graph.compile()


#: The compiled agent graph. Compiled once at import: it holds no per-run state, so every call
#: reuses it and only the invocation payload differs.
AGENT_GRAPH = build_graph()


def run_agent(question: str, handle: DatasetHandle, client=None) -> AgentResult:
    started = time.perf_counter()
    settings = Settings.from_env()
    client = client or build_model_client(settings)

    final = AGENT_GRAPH.invoke(
        {
            "question": question,
            "handle": handle,
            "client": client,
            "settings": settings,
            "stalled": False,
            "states": [],
            "model_calls": 0,
            "retries": 0,
        }
    )

    result: QueryResult = final["result"]
    verification: Verification = final["verification"]
    trace = Trace(
        states=list(final["states"]),
        retries=final["retries"],
        model_calls=final["model_calls"],
        provider=client.provider,
        model=client.model,
    )
    trace.status = "success" if verification.consistent else "failed"
    trace.duration_ms = (time.perf_counter() - started) * 1000
    _record_usage(trace, client, settings)
    return AgentResult(
        sql=final["sql"],
        query_result=result,
        chart=final["chart"],
        conclusion=final["conclusion"],
        verification=verification,
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
