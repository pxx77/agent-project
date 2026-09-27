"""检索策略的端到端回归：默认（词面）不退化，融合不越过拒答下限。

`evals/retrieval_ablation.json` 记录了四种策略的实测对比；这里把其中必须长期成立的四条
不变量固化成断言，免得改了编码器或融合参数以后，默认策略悄悄变差却没人发现。

指标口径直接复用 `evals/run_eval.py` 的 `evaluate()`，所以这里的数字与 `report.json`
逐字段可比——如果这里另写一套算法，这组断言就会和报告说的不是同一件事。
"""

import json
import sys
from pathlib import Path

import pytest

from citeguard.config import Settings
from citeguard.embeddings import HashingEmbedder
from citeguard.llm import MockClient
from citeguard.retrieval import BM25Index, HybridIndex, VectorIndex
from citeguard.workflow import FixtureAgent, build_fixture_agent

EVALS = Path(__file__).resolve().parents[1] / "evals"
# `evals/` 不是包，与 `run_retrieval_ablation.py` 用的是同一套显式 sys.path 做法。
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

from run_eval import CORPUS, evaluate  # noqa: E402

CASES = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scores() -> dict[str, dict]:
    """Score the three retrievers end to end over the real corpus, once per module."""
    base = build_fixture_agent(CORPUS)
    chunks = base.index.chunks
    settings = Settings.from_env()
    embedder = HashingEmbedder(settings.embedding_dim)

    retrievers = {
        "lexical": BM25Index(chunks),
        "dense": VectorIndex(chunks, embedder),
        "hybrid": HybridIndex(chunks, embedder, min_coverage=settings.retrieval_min_coverage),
    }
    scored: dict[str, dict] = {}
    for label, index in retrievers.items():
        agent = FixtureAgent(index, document_names=base.document_names)
        _, metrics = evaluate(CASES, agent, MockClient())
        scored[label] = metrics
    return scored


def test_the_default_strategy_keeps_every_case_correct(scores):
    """默认（词面）策略保持满分：任何改动让默认路径丢用例，都要先在这里被拦住。"""
    assert scores["lexical"]["case_accuracy"] == 1.0
    assert scores["lexical"]["cross_document_coverage"] == 1.0


def test_the_default_strategy_keeps_refusing_questions_without_evidence(scores):
    assert scores["lexical"]["insufficient_evidence_rate"] == 1.0
    assert scores["lexical"]["unsupported_claims_total"] == 0


def test_fusion_does_not_beat_bm25_under_the_offline_encoder(scores):
    """这条是实测结论，不是通用断言：离线哈希编码器与 BM25 看的是同一类信号。

    因此融合只可能重排、不可能补充新信息，`auto` 才不会在离线配置下打开融合。
    接入真实 embedding 服务后这条不再适用，届时应改写本用例而不是放宽断言。
    """
    assert scores["hybrid"]["case_accuracy"] <= scores["lexical"]["case_accuracy"]
    # 融合仍受词面下限约束：不能因为多了一条通道就放行「证据不足」的问题。
    assert scores["hybrid"]["insufficient_evidence_rate"] == 1.0


def test_the_dense_channel_alone_loses_the_refusal(scores):
    """稠密通道单独使用时不具备拒答判据——这正是保留词面下限作为拒答门槛的理由。"""
    assert scores["dense"]["insufficient_evidence_rate"] == 0.0
    assert scores["dense"]["case_accuracy"] < scores["lexical"]["case_accuracy"]


def test_refusing_is_also_cheaper_than_answering_without_evidence(scores):
    # 拒答省掉「回答 + 逐条核验」两次调用，因此失去拒答能力的策略 token 与费用都更高。
    assert scores["dense"]["tokens_total"] > scores["lexical"]["tokens_total"]
    assert scores["dense"]["cost_cny_total"] > scores["lexical"]["cost_cny_total"]
