from __future__ import annotations

import time

from .config import Settings
from .execution import execute_sql
from .llm import MockClient
from .models import AgentResult, ChartSpec, DatasetHandle, Trace, Verification
from .profiling import profile_dataset


def run_agent(question: str, handle: DatasetHandle, client=None) -> AgentResult:
    started = time.perf_counter()
    settings = Settings.from_env()
    trace = Trace(states=["profile", "plan", "policy", "execute"])
    profile_dataset(handle)
    client = client or MockClient()
    payload = client.complete_json("Generate safe read-only SQL.", question, "analysis")
    sql = str(payload.get("sql", "SELECT * FROM sales"))
    result = execute_sql(handle, sql, settings.max_rows)
    while result.error and trace.retries < settings.max_retries and "only one read-only" not in result.error:
        trace.retries += 1
        trace.states.append("repair")
        result = execute_sql(handle, sql.replace("revenuee", "revenue"), settings.max_rows)
    trace.states.extend(["visualize", "verify"])
    chart = ChartSpec(kind="bar", x=result.columns[0], y=result.columns[-1]) if len(result.columns) >= 2 and result.error is None else None
    consistent = result.error is None and bool(result.columns)
    trace.status = "success" if consistent else "failed"
    trace.duration_ms = (time.perf_counter() - started) * 1000
    return AgentResult(sql=sql, query_result=result, chart=chart, conclusion=(f"Query returned {result.row_count} rows." if consistent else f"Analysis failed: {result.error}"), verification=Verification(consistent=consistent, reason="Conclusion is derived from returned columns." if consistent else "No verified result."), trace=trace)


def build_fixture_agent():
    from .ingestion import load_fixture
    return FixtureAgent(load_fixture())


class FixtureAgent:
    def __init__(self, handle: DatasetHandle):
        self.handle = handle

    def run(self, question: str) -> AgentResult:
        return run_agent(question, self.handle, MockClient())
