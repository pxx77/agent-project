"""DataPilot 评测脚本。

默认离线运行：用确定性的 MockClient 与脚本化探针，在 `fixtures/` 的真实数据集上跑完整
工作流（画像 -> 生成 SQL -> 只读策略校验 -> 执行 -> 修复重试 -> 图表 -> 结论 -> 验证）。
加上 `--live` 且环境中存在 `DEEPSEEK_API_KEY` 时，`mock` 类样例改用真实 DeepSeek 生成 SQL，
用于记录线上结果；`scripted` 类的管线探针仍使用脚本化 SQL。

指标全部来自真实运行结果，脚本内没有任何硬编码分数。

用法：
    uv run python evals/run_eval.py
    uv run python evals/run_eval.py --live
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from datapilot.config import Settings
from datapilot.llm import MockClient, build_model_client, record_estimate
from datapilot.models import TokenUsage
from datapilot.usage import PRICE_CURRENCY, PRICE_SOURCE, PRICE_UNIT, PRICE_VERIFIED_ON, UsageLedger
from datapilot.workflow import build_fixture_agent

EVALS = Path(__file__).resolve().parent


class ScriptedClient:
    """Return one fixed SQL string for the planning call and reuse the real repair logic.

    The repair probe depends on the actual ``sql_repair`` behaviour, so the repair branch is
    delegated to :class:`MockClient` instead of being reimplemented here.

    It keeps its own usage ledger so the scripted cases carry token and cost figures like the mock
    ones: the pipeline still builds a real prompt of a measurable size, even though the SQL that
    comes back is fixed. Leaving those cases at zero would make the report's total an undercount.
    """

    provider = "scripted"
    model = "scripted-sql"

    def __init__(self, sql: str):
        self.sql = sql
        self._ledger = UsageLedger()
        self._repair = MockClient()

    def consume_usage(self) -> TokenUsage:
        """Hand out the usage booked since the last call and reset, so callers can attribute it."""
        return self._ledger.drain()

    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        if schema_name == "sql_repair":
            payload = self._repair.complete_json(system, user, schema_name)
        else:
            payload = {"sql": self.sql}
        record_estimate(self._ledger, system, user, payload)
        return payload


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _matches_top(result, expected_top: list | None) -> bool:
    """Check the leading row against the benchmarked answer, ignoring float formatting."""
    if not expected_top:
        return True
    if not result.rows:
        return False
    head = result.rows[0]
    if str(head[0]) != str(expected_top[0]):
        return False
    try:
        return abs(float(head[-1]) - float(expected_top[1])) < 0.01
    except (TypeError, ValueError):
        return False


def _judge(case: dict, result) -> bool:
    """Decide whether the run matched the case expectation, using only observed output."""
    query = result.query_result
    expectation = case["expectation"]
    if expectation == "analysis":
        return (
            query.error is None
            and not query.policy_blocked
            and query.row_count == case.get("expected_rows", query.row_count)
            and _matches_top(query, case.get("expected_top"))
        )
    if expectation == "blocked":
        return bool(query.policy_blocked) and query.error is not None
    if expectation == "repaired":
        return (
            query.error is None
            and result.trace.retries >= 1
            and query.row_count == case.get("expected_rows", query.row_count)
            and _matches_top(query, case.get("expected_top"))
        )
    if expectation == "failed":
        return (
            query.error is not None
            and not query.policy_blocked
            and result.trace.retries <= Settings.from_env().max_retries
        )
    raise ValueError(f"unknown expectation: {expectation}")


def _run_case(case: dict, agents: dict, default_client) -> dict:
    agent = agents[case["dataset"]]
    client = ScriptedClient(case["scripted_sql"]) if case["client"] == "scripted" else default_client
    result = agent.run(case["question"], client)
    query = result.query_result
    return {
        "id": case["id"],
        "dataset": case["dataset"],
        "question": case["question"],
        "client": case["client"],
        "expectation": case["expectation"],
        "sql": result.sql,
        "error": query.error,
        "policy_blocked": query.policy_blocked,
        "columns": query.columns,
        "row_count": query.row_count,
        "top_row": query.rows[0] if query.rows else None,
        "chart": result.chart.model_dump() if result.chart else None,
        "conclusion": result.conclusion,
        "status": result.trace.status,
        "retries": result.trace.retries,
        "model_calls": result.trace.model_calls,
        "latency_ms": round(result.trace.duration_ms, 3),
        "prompt_tokens": result.trace.usage.prompt_tokens,
        "completion_tokens": result.trace.usage.completion_tokens,
        "total_tokens": result.trace.usage.total_tokens,
        "usage_measured": result.trace.usage.measured,
        "billing_model": result.trace.billing_model,
        "cost_cny": result.trace.cost_cny,
        "correct": _judge(case, result),
    }


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the DataPilot evaluation suite.")
    parser.add_argument("--live", action="store_true", help="use the real DeepSeek model for mock-type cases")
    parser.add_argument("--output", default=None, help="report path (default: evals/report.json)")
    args = parser.parse_args()

    if args.live:
        client = build_model_client()
        if isinstance(client, MockClient):
            print("--live 被忽略：环境中没有可用的 DEEPSEEK_API_KEY，本次仍为离线回放。")
    else:
        client = MockClient()

    cases = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))
    agents = {name: build_fixture_agent(name) for name in {case["dataset"] for case in cases}}
    rows = [_run_case(case, agents, client) for case in cases]

    by_expectation = {
        kind: [row for row in rows if row["expectation"] == kind]
        for kind in ("analysis", "blocked", "repaired", "failed")
    }
    latencies = [row["latency_ms"] for row in rows]
    costs = [row["cost_cny"] or 0.0 for row in rows]

    metrics = {
        "case_accuracy": _rate(sum(row["correct"] for row in rows), len(rows)),
        "execution_success_rate": _rate(
            sum(row["error"] is None for row in by_expectation["analysis"]),
            len(by_expectation["analysis"]),
        ),
        "grounded_result_rate": _rate(
            sum(row["correct"] for row in by_expectation["analysis"]), len(by_expectation["analysis"])
        ),
        "unsafe_block_rate": _rate(
            sum(bool(row["policy_blocked"]) for row in by_expectation["blocked"]),
            len(by_expectation["blocked"]),
        ),
        "repair_success_rate": _rate(
            sum(row["correct"] for row in by_expectation["repaired"]), len(by_expectation["repaired"])
        ),
        "bounded_failure_rate": _rate(
            sum(row["correct"] for row in by_expectation["failed"]), len(by_expectation["failed"])
        ),
        "mean_latency_ms": round(sum(latencies) / len(latencies), 3),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
        "model_calls_total": sum(row["model_calls"] for row in rows),
        "prompt_tokens_total": sum(row["prompt_tokens"] for row in rows),
        "completion_tokens_total": sum(row["completion_tokens"] for row in rows),
        "tokens_total": sum(row["total_tokens"] for row in rows),
        "cost_cny_total": round(sum(costs), 6),
        "cost_cny_per_case": round(sum(costs) / len(rows), 6),
        # Conjunctive: one estimated case is enough to mark the whole run as estimated.
        "usage_measured": all(row["usage_measured"] for row in rows),
    }

    measured = metrics["usage_measured"]
    usage = {
        "measured": measured,
        "prompt_tokens": metrics["prompt_tokens_total"],
        "completion_tokens": metrics["completion_tokens_total"],
        "total_tokens": metrics["tokens_total"],
        "cost_cny": metrics["cost_cny_total"],
        "cost_cny_per_case": metrics["cost_cny_per_case"],
        "pricing_model": rows[0]["billing_model"] if rows else "",
        "currency": PRICE_CURRENCY,
        "unit": PRICE_UNIT,
        "price_source": PRICE_SOURCE,
        "price_verified_on": PRICE_VERIFIED_ON,
        "note": (
            "离线回放不产生真实计费：token 数按官方字符换算比例在本机估算（measured=false），"
            "费用按 pricing_model 的官方单价折算，只能作为量级参考。"
            if not measured
            else "线上运行，token 数取自 DeepSeek 响应体的 usage 字段，费用按官方单价换算。"
        ),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": client.provider,
        "model": client.model,
        "mode": "live" if not isinstance(client, MockClient) else "mock",
        "datasets": sorted(agents),
        "case_count": len(rows),
        "notes": (
            "离线模式下 mock 类样例使用确定性模板客户端，scripted 类样例使用脚本化 SQL 探针，"
            "因此这些数字衡量的是管线本身（只读策略、执行、修复重试、图表、结论、验证），"
            "而不是模型生成 SQL 的质量；--live 时 mock 类样例改由 DeepSeek 生成 SQL。"
        ),
        "metrics": metrics,
        "usage": usage,
        "cases": rows,
    }

    output = Path(args.output) if args.output else EVALS / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("mode", "provider", "model", "datasets", "case_count", "metrics")}, ensure_ascii=False, indent=2))
    print(f"\nusage: {json.dumps(usage, ensure_ascii=False)}")
    failed = [row["id"] for row in rows if not row["correct"]]
    print(f"\n未通过样例: {failed or '无'}")
    print(f"\nreport -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
