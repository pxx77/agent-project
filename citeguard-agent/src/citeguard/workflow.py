from __future__ import annotations

import json
import time

from .config import Settings
from .llm import ModelClient, build_model_client
from .models import AgentResult, Chunk, Claim, TokenUsage, Trace, Verification
from .retrieval import Retriever, build_index
from .usage import cost_of, drain_usage

ANSWER_INSTRUCTION = (
    "只允许依据证据回答。返回 JSON："
    '{"answer": "...", "claims": [{"text": "...", "citations": ["证据编号"]}]}。'
    "claims 中每条论断都必须能由 citations 指向的原文证据直接支持，不得编造证据。"
)

VERIFY_INSTRUCTION = (
    "你是严格的引用核验器。只判断 claim 是否被 evidence 直接支持，不要使用你自己的知识。"
    '返回 JSON：{"supported": true 或 false, "reason": "一句话理由"}。'
    "只要 claim 的关键部分在 evidence 中找不到依据，就必须返回 false。"
)


def _evidence_payload(evidence: list[Chunk]) -> list[dict]:
    """Serialize retrieved chunks into the shape both model calls expect."""
    return [{"id": chunk.id, "text": chunk.text} for chunk in evidence]


def _record_usage(trace: Trace, client: ModelClient | None, settings: Settings) -> None:
    """Attach the tokens this run consumed, and what they cost.

    The drain is per run rather than per client lifetime, because the evaluation reuses one client
    across every case: a lifetime counter would report the whole run's tokens on the first case and
    double-count from there.

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


def _collect_claims(payload: dict, answer: str, evidence: list[Chunk]) -> list[Claim]:
    """Use the model's discrete claims when present, otherwise treat the answer as one claim."""
    default_citations = [chunk.id for chunk in evidence[:2]]
    claims: list[Claim] = []
    raw_claims = payload.get("claims")
    if isinstance(raw_claims, list):
        for item in raw_claims:
            text = item.get("text") if isinstance(item, dict) else item
            if not isinstance(text, str) or not text.strip():
                continue
            declared = item.get("citations") if isinstance(item, dict) else None
            citations = [value for value in declared if isinstance(value, str)] if isinstance(declared, list) else []
            claims.append(Claim(text=text.strip(), citations=citations or default_citations))
    if not claims:
        claims = [Claim(text=answer, citations=default_citations)]
    return claims


def _verify_claim(claim: Claim, evidence: list[Chunk], client: ModelClient) -> tuple[Claim, bool]:
    """Judge one claim against the chunks it cites, returning the claim and whether a model call happened."""
    cited = [chunk for chunk in evidence if chunk.id in claim.citations]
    if not cited:
        # An unresolvable citation cannot be supported, and there is nothing to judge against.
        return claim.model_copy(update={"status": "unsupported"}), False
    payload = client.complete_json(
        VERIFY_INSTRUCTION,
        json.dumps({"claim": claim.text, "evidence": _evidence_payload(cited)}, ensure_ascii=False),
        "claim_verification",
    )
    supported = payload.get("supported")
    if not isinstance(supported, bool):
        status = "uncertain"
    else:
        status = "supported" if supported else "unsupported"
    return claim.model_copy(update={"status": status}), True


def run_agent(question: str, index: Retriever, client: ModelClient | None = None) -> AgentResult:
    started = time.perf_counter()
    settings = Settings.from_env()
    trace = Trace(
        states=["plan", "retrieve"],
        provider="not_called",
        model="not-called",
        retriever=index.name,
    )
    evidence = index.search(question, limit=5)
    trace.retrieved_chunk_ids = [chunk.id for chunk in evidence]
    if not evidence:
        trace.status = "insufficient_evidence"
        trace.states.append("verify")
        trace.duration_ms = (time.perf_counter() - started) * 1000
        # No model call happened, so this drains an empty ledger and prices nothing.
        _record_usage(trace, client, settings)
        return AgentResult(answer="Insufficient evidence in the indexed documents.", verification=Verification(), trace=trace)
    client = client or build_model_client(settings)
    trace.provider = client.provider
    trace.model = client.model
    trace.states.extend(["answer", "verify"])
    payload = client.complete_json(
        ANSWER_INSTRUCTION,
        json.dumps({"question": question, "evidence": _evidence_payload(evidence)}, ensure_ascii=False),
        "citation_answer",
    )
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Model response is missing a non-empty 'answer' field")
    answer = answer.strip()
    claims = _collect_claims(payload, answer, evidence)
    verified: list[Claim] = []
    verification_calls = 0
    for claim in claims:
        judged, called = _verify_claim(claim, evidence, client)
        verified.append(judged)
        verification_calls += int(called)
    trace.model_calls = 1 + verification_calls
    supported = sum(1 for claim in verified if claim.status == "supported")
    unsupported = sum(1 for claim in verified if claim.status == "unsupported")
    verification = Verification(
        supported_claims=supported,
        unsupported_claims=unsupported,
        support_rate=supported / len(verified) if verified else 0.0,
    )
    if unsupported and not supported:
        trace.status = "unsupported"
    elif unsupported:
        trace.status = "partial_support"
    else:
        trace.status = "success"
    trace.duration_ms = (time.perf_counter() - started) * 1000
    _record_usage(trace, client, settings)
    citations = list(dict.fromkeys(citation for claim in verified for citation in claim.citations))
    return AgentResult(
        answer=answer,
        claims=verified,
        citations=citations,
        evidence=evidence,
        verification=verification,
        trace=trace,
    )


def build_fixture_agent(corpus: tuple[str, ...] | list[str] | None = None) -> "FixtureAgent":
    """Build an agent over the bundled fixtures.

    Without ``corpus`` the agent indexes the single short smoke document, which keeps the
    offline tests quick. Passing file names from ``fixtures/`` indexes that corpus instead;
    ``evals/run_eval.py`` uses this so the reported numbers come from the real documents.
    """
    from pathlib import Path

    from .ingestion import chunk_document, parse_bytes
    from .models import Document

    if corpus is None:
        chunks = chunk_document(
            Document(
                id="fixture",
                name="ai_agents.md",
                text=(
                    "智能体评测对发布提出了明确要求：必须检查任务成功率、引用支撑、响应延迟和成本。"
                    "Agent evaluation measures task success, citation support, latency, and cost. "
                    "Reliable agents use bounded retries and explicit verification."
                ),
            ),
            size=180,
            overlap=20,
        )
        return FixtureAgent(build_index(chunks))

    fixtures = Path(__file__).resolve().parents[2] / "fixtures"
    chunks: list[Chunk] = []
    names: dict[str, str] = {}
    for name in corpus:
        document = parse_bytes(name, (fixtures / name).read_bytes())
        names[document.id] = document.name
        chunks.extend(chunk_document(document))
    return FixtureAgent(build_index(chunks), document_names=names)


class FixtureAgent:
    def __init__(self, index: Retriever, document_names: dict[str, str] | None = None):
        self.index = index
        self.document_names = document_names or {}

    def run(self, question: str, client=None) -> AgentResult:
        return run_agent(question, self.index, client)
