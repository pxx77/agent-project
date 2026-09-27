"""CiteGuard 评测脚本。

默认离线运行：用确定性的 MockClient 在 `fixtures/` 的真实文档上跑完整工作流。
加上 `--live` 且环境中存在 `DEEPSEEK_API_KEY` 时改用真实 DeepSeek，用于记录线上结果。

用法：
    uv run python evals/run_eval.py
    uv run python evals/run_eval.py --live
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from citeguard.llm import MockClient, build_model_client
from citeguard.workflow import run_agent

EVALS = Path(__file__).resolve().parent
CORPUS = ("agent_evaluation_handbook.md", "agent_incident_policy.txt", "ai_agents.md")


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _run_case(case: dict, agent, client) -> dict:
    """Run one case and derive every field from the actual run, never from the case file."""
    result = agent.run(case["question"], client)
    document_of = {chunk.id: chunk.document_id for chunk in agent.index.chunks}
    resolved = [citation for citation in result.citations if citation in document_of]
    sources = sorted(
        {agent.document_names.get(document_of[citation], document_of[citation]) for citation in resolved}
    )
    expected = sorted(case.get("expected_sources") or [])
    insufficient = result.trace.status == "insufficient_evidence"
    if case["expectation"] == "insufficient":
        correct = insufficient
    else:
        # An answer only counts when it cites every document the question requires, so a
        # half-answered cross-document question cannot pass on a single document.
        correct = (
            not insufficient
            and bool(result.citations)
            and bool(expected)
            and set(expected) <= set(sources)
        )
    return {
        "id": case["id"],
        "question": case["question"],
        "expectation": case["expectation"],
        "expected_sources": expected,
        "citations": result.citations,
        "resolved_citations": resolved,
        "sources": sources,
        "status": result.trace.status,
        "support_rate": round(result.verification.support_rate, 4),
        "supported_claims": result.verification.supported_claims,
        "unsupported_claims": result.verification.unsupported_claims,
        "model_calls": result.trace.model_calls,
        "retrieved_chunks": len(result.trace.retrieved_chunk_ids),
        "latency_ms": round(result.trace.duration_ms, 3),
        "correct": correct,
    }


def _verification_probe(index) -> dict:
    """Show that the verifier rejects fabricated and unresolvable claims instead of approving them.

    The probe feeds three claims through the real workflow: one grounded in a retrieved chunk,
    one that cites a real chunk but asserts something it does not contain, and one whose
    citation cannot be resolved at all. A verifier that always answers "supported" fails here.
    """
    judge = MockClient()
    calls = {"verifier": 0}

    class ProbeClient:
        provider = "probe"
        model = "scripted-claims"

        def complete_json(self, system: str, user: str, schema_name: str) -> dict:
            if schema_name == "claim_verification":
                calls["verifier"] += 1
                return judge.complete_json(system, user, schema_name)
            evidence = json.loads(user).get("evidence") or []
            anchor = evidence[0]["id"]
            return {
                "answer": "three probe claims",
                "claims": [
                    {"text": evidence[0]["text"], "citations": [anchor]},
                    {"text": "平台必须把审计材料保存 5000 天。", "citations": [anchor]},
                    {"text": "A claim whose citation does not exist.", "citations": ["missing:999"]},
                ],
            }

    result = run_agent("证伪探针：核验器能否拒绝伪造与悬空引用？", index, ProbeClient())
    supported = result.verification.supported_claims
    unsupported = result.verification.unsupported_claims
    document_of = {chunk.id for chunk in index.chunks}
    total_citations = len(result.citations)
    resolved_citations = sum(citation in document_of for citation in result.citations)
    resolvability = round(resolved_citations / total_citations, 4) if total_citations else 0.0
    return {
        "supported_claims": supported,
        "unsupported_claims": unsupported,
        "support_rate": round(result.verification.support_rate, 4),
        "status": result.trace.status,
        "verifier_calls": calls["verifier"],
        "citations": result.citations,
        "citation_resolvability": resolvability,
        "passed": supported == 1 and unsupported == 2 and resolvability < 1.0,
        "expectation": (
            "1 supported, 2 unsupported, verifier called twice, "
            "and the dangling citation drags citation_resolvability below 1.0"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the CiteGuard evaluation suite.")
    parser.add_argument("--live", action="store_true", help="use the real DeepSeek model instead of MockClient")
    parser.add_argument("--output", default=None, help="report path (default: evals/report.json)")
    args = parser.parse_args()

    from citeguard.workflow import build_fixture_agent

    corpus = {"corpus": list(CORPUS)}
    agent = build_fixture_agent(CORPUS)
    if args.live:
        client = build_model_client()
        if isinstance(client, MockClient):
            print("--live 被忽略：环境中没有可用的 DEEPSEEK_API_KEY，本次仍为离线回放。")
    else:
        client = MockClient()

    cases = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))
    rows = [_run_case(case, agent, client) for case in cases]

    answerable = [row for row in rows if row["expectation"] == "answerable"]
    insufficient_cases = [row for row in rows if row["expectation"] == "insufficient"]
    cross_document = [row for row in answerable if len(row["expected_sources"]) > 1]
    all_citations = sum(len(row["citations"]) for row in rows)
    resolved_citations = sum(len(row["resolved_citations"]) for row in rows)
    latencies = [row["latency_ms"] for row in rows]

    metrics = {
        "case_accuracy": round(sum(row["correct"] for row in rows) / len(rows), 4),
        "answerable_grounded_rate": round(
            sum(bool(row["citations"]) for row in answerable) / len(answerable), 4
        ) if answerable else 0.0,
        "insufficient_evidence_rate": round(
            sum(row["status"] == "insufficient_evidence" for row in insufficient_cases)
            / len(insufficient_cases), 4
        ) if insufficient_cases else 0.0,
        "cross_document_coverage": round(
            sum(set(row["expected_sources"]) <= set(row["sources"]) for row in cross_document)
            / len(cross_document), 4
        ) if cross_document else 0.0,
        "citation_resolvability": round(resolved_citations / all_citations, 4) if all_citations else 0.0,
        "mean_support_rate": round(sum(row["support_rate"] for row in rows) / len(rows), 4),
        "answerable_case_count": len(answerable),
        "insufficient_case_count": len(insufficient_cases),
        "unsupported_claims_total": sum(row["unsupported_claims"] for row in rows),
        "mean_latency_ms": round(sum(latencies) / len(latencies), 3),
        "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
        "model_calls_total": sum(row["model_calls"] for row in rows),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": client.provider,
        "model": client.model,
        "mode": "live" if not isinstance(client, MockClient) else "mock",
        **corpus,
        "chunk_count": len(agent.index.chunks),
        "case_count": len(rows),
        "metrics": metrics,
        "cases": rows,
        "verification_probe": _verification_probe(agent.index),
    }

    output = Path(args.output) if args.output else EVALS / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("mode", "provider", "model", "case_count", "metrics")}, ensure_ascii=False, indent=2))
    print(f"\nverification_probe: {json.dumps(report['verification_probe'], ensure_ascii=False)}")
    print(f"\nreport -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
