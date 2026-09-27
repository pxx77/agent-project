"""CiteGuard 检索策略消融。

用同一份 `fixtures/` 语料、同一套 `cases.json` 用例和同一个确定性 MockClient，
端到端比较四种检索策略：

===============  ==========================================================
lexical          BM25（词面）——项目此前的唯一策略
dense            哈希向量余弦（稠密通道单独使用）
hybrid           BM25 与稠密通道 RRF 融合，词面下限仍决定「证据不足」
hybrid+augment   hybrid 且允许稠密通道补入词面下限拒绝过的文本块
===============  ==========================================================

指标口径直接复用 `evals/run_eval.py` 的 `evaluate()`，所以这里的数字与 `report.json`
逐字段可比；唯一新增的是检索层的 `document_recall_at_5`。

为什么单独一个脚本：检索层的「文档召回」在这份小语料上会饱和（3 篇文档、长文本块，
top-5 几乎总能覆盖全部相关文档），所以只看召回率无法区分策略；能区分的是端到端结果，
因此这里跑完整工作流而不是只比对检索命中。

用法：
    uv run python evals/run_retrieval_ablation.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).resolve().parent
# The sibling evaluation module is not a package import; running this file as a script puts
# its own directory on sys.path, but being explicit keeps it working when imported too.
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

from run_eval import evaluate  # noqa: E402

from citeguard.config import Settings  # noqa: E402
from citeguard.embeddings import HashingEmbedder  # noqa: E402
from citeguard.llm import MockClient  # noqa: E402
from citeguard.retrieval import BM25Index, HybridIndex, VectorIndex  # noqa: E402
from citeguard.workflow import FixtureAgent, build_fixture_agent  # noqa: E402

CORPUS = ("agent_evaluation_handbook.md", "agent_incident_policy.txt", "ai_agents.md")


def _retrieval_metrics(index, cases: list[dict], document_names: dict[str, str]) -> dict:
    """Retrieval-only metrics, measured on the same top-5 the workflow would receive."""
    hits = 0
    total = 0
    returned: list[int] = []
    for case in cases:
        expected = set(case.get("expected_sources") or [])
        chunks = index.search(case["question"], 5)
        returned.append(len(chunks))
        if not expected:
            continue
        total += 1
        documents = {document_names.get(chunk.document_id, chunk.document_id) for chunk in chunks}
        hits += int(expected <= documents)
    return {
        "document_recall_at_5": round(hits / total, 4) if total else 0.0,
        "mean_chunks_returned": round(sum(returned) / len(returned), 3) if returned else 0.0,
    }


def _row(name: str, index, cases: list[dict], document_names: dict[str, str]) -> dict:
    """Score one retriever through the real workflow, using the shared metric definitions."""
    agent = FixtureAgent(index, document_names=document_names)
    _, metrics = evaluate(cases, agent, MockClient())
    return {
        "retriever": name,
        "metrics": metrics,
        "retrieval": _retrieval_metrics(index, cases, document_names),
    }


def main() -> int:
    settings = Settings.from_env()
    base = build_fixture_agent(CORPUS)
    chunks = base.index.chunks
    document_names = base.document_names
    cases = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))
    embedder = HashingEmbedder(settings.embedding_dim)
    floor = settings.retrieval_min_coverage

    retrievers = {
        "lexical": BM25Index(chunks),
        "dense": VectorIndex(chunks, embedder),
        "hybrid": HybridIndex(chunks, embedder, min_coverage=floor),
        "hybrid+augment": HybridIndex(chunks, embedder, min_coverage=floor, admit_dense_only=True),
    }
    rows = [_row(name, index, cases, document_names) for name, index in retrievers.items()]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "mock",
        "encoder": f"hashing(dim={settings.embedding_dim})",
        "fusion": "rrf",
        "min_coverage": floor,
        "chunk_count": len(chunks),
        "case_count": len(cases),
        "retrievers": rows,
        "note": (
            "哈希编码器是词面编码器：它与 BM25 看的是同一类信号，因此融合不带来语义增益，"
            "在 cross-document-audit-retention 上还会打乱 BM25 已排序正确的 top-5，"
            "使 case_accuracy 从 1.0 降到 0.9583、cross_document_coverage 从 1.0 降到 0.6667。"
            "dense 单独使用时无法判断『证据不足』：三个无关问题全部被作答，"
            "insufficient_evidence_rate 为 0，这是保留 BM25 词面下限作为拒答判据的原因。"
            "拒答同时更省钱：dense 把三个无依据问题也走完「回答 + 逐条核验」，"
            "token 从 56463 升到 65401（+15.8%），费用从 0.07148 元升到 0.08046 元（+12.6%），"
            "所以词面下限不只是正确性措施，也是一项成本措施。"
            "接入真实 embedding 服务（CITEGUARD_EMBEDDING_PROVIDER=http）后稠密通道才承载语义信息，"
            "届时可用 CITEGUARD_RETRIEVER=hybrid 启用融合。"
        ),
    }
    output = EVALS / "retrieval_ablation.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = [
        {
            "retriever": row["retriever"],
            "case_accuracy": row["metrics"]["case_accuracy"],
            "cross_document_coverage": row["metrics"]["cross_document_coverage"],
            "insufficient_evidence_rate": row["metrics"]["insufficient_evidence_rate"],
            "document_recall_at_5": row["retrieval"]["document_recall_at_5"],
            "tokens_total": row["metrics"]["tokens_total"],
            "cost_cny_total": row["metrics"]["cost_cny_total"],
        }
        for row in rows
    ]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nablation -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
