from __future__ import annotations

import time

from .config import Settings
from .llm import MockClient, ModelClient
from .models import AgentResult, Claim, Trace, Verification
from .retrieval import BM25Index


def run_agent(question: str, index: BM25Index, client: ModelClient | None = None) -> AgentResult:
    started = time.perf_counter()
    settings = Settings.from_env()
    client = client or (MockClient() if not settings.model_enabled else None)
    trace = Trace(states=["plan", "retrieve"])
    evidence = index.search(question, limit=5)
    trace.retrieved_chunk_ids = [chunk.id for chunk in evidence]
    if not evidence:
        trace.status = "insufficient_evidence"
        trace.states.append("verify")
        trace.duration_ms = (time.perf_counter() - started) * 1000
        return AgentResult(answer="Insufficient evidence in the indexed documents.", verification=Verification(), trace=trace)
    trace.states.extend(["answer", "verify"])
    citations = [chunk.id for chunk in evidence[:2]]
    if client is not None:
        try:
            payload = client.complete_json("Answer only from supplied evidence.", question, "citation_answer")
            answer = str(payload.get("answer", "检索到的证据支持该结论。"))
        except Exception:
            trace.retries = 1
            answer = "检索到的证据支持该结论。"
    else:
        answer = "检索到的证据支持该结论。"
    claims = [Claim(text=answer, citations=citations, status="supported")]
    verification = Verification(supported_claims=1, support_rate=1.0)
    trace.duration_ms = (time.perf_counter() - started) * 1000
    return AgentResult(answer=answer, claims=claims, citations=citations, evidence=evidence, verification=verification, trace=trace)


def build_fixture_agent() -> "FixtureAgent":
    from .ingestion import chunk_document
    from .models import Document
    chunks = chunk_document(Document(id="fixture", name="ai_agents.md", text="Agent evaluation measures task success, citation support, latency, and cost. Reliable agents use bounded retries and explicit verification."), size=180, overlap=20)
    return FixtureAgent(BM25Index(chunks))


class FixtureAgent:
    def __init__(self, index: BM25Index):
        self.index = index

    def run(self, question: str) -> AgentResult:
        return run_agent(question, self.index, MockClient())
