from __future__ import annotations

import json
from typing import Protocol

import httpx

from .config import Settings
from .models import TokenUsage
from .retrieval import terms
from .usage import UsageLedger, estimated_usage, usage_from_response


class ModelClient(Protocol):
    provider: str
    model: str

    def complete_json(self, system: str, user: str, schema_name: str) -> dict: ...

    def consume_usage(self) -> TokenUsage: ...


def _loads(user: str) -> dict:
    try:
        payload = json.loads(user)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _record_estimate(ledger: UsageLedger, system: str, user: str, payload: dict) -> None:
    """Book one offline call as an estimate, measured from the text that actually crossed over."""
    ledger.record(estimated_usage(f"{system}\n{user}", json.dumps(payload, ensure_ascii=False)))


def _lexical_verdict(payload: dict) -> dict:
    """Judge a claim by checking whether the cited evidence covers every claim term."""
    claim_terms = terms(str(payload.get("claim", "")))
    evidence_terms: set[str] = set()
    for item in payload.get("evidence") or []:
        if isinstance(item, dict):
            evidence_terms |= terms(str(item.get("text", "")))
    if not claim_terms:
        return {"supported": False, "reason": "claim contained no comparable terms"}
    if claim_terms <= evidence_terms:
        return {"supported": True, "reason": "every claim term appears in the cited evidence"}
    missing = sorted(claim_terms - evidence_terms)
    return {"supported": False, "reason": f"cited evidence is missing claim terms: {missing[:5]}"}


class MockClient:
    """Deterministic offline client.

    It answers by quoting the retrieved evidence and then judges each claim against that
    evidence with a lexical containment check, so offline runs exercise the same
    answer-then-verify contract as the real model instead of returning a fixed verdict.

    It also books token usage, so the offline evaluation can exercise the cost accounting. Those
    counts are estimates and are flagged as such — a replay does not reach a provider and
    therefore cannot produce a measured count.
    """

    provider = "mock"
    model = "deterministic-fixture"

    def __init__(self) -> None:
        self._ledger = UsageLedger()

    def consume_usage(self) -> TokenUsage:
        """Hand out the usage booked since the last call and reset, so callers can attribute it."""
        return self._ledger.drain()

    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        payload = self._respond(system, user, schema_name)
        _record_estimate(self._ledger, system, user, payload)
        return payload

    def _respond(self, system: str, user: str, schema_name: str) -> dict:
        payload = _loads(user)
        if schema_name == "claim_verification":
            return _lexical_verdict(payload)
        answer = ""
        for item in payload.get("evidence") or []:
            text = item.get("text") if isinstance(item, dict) else None
            if isinstance(text, str) and text.strip():
                answer = text.strip()
                break
        if not answer:
            answer = "检索到的证据支持该结论。"
        return {"answer": answer, "claims": [{"text": answer, "citations": []}]}


class DeepSeekClient:
    provider = "deepseek"

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self._ledger = UsageLedger()

    def consume_usage(self) -> TokenUsage:
        return self._ledger.drain()

    def complete_json(self, system: str, user: str, schema_name: str) -> dict:
        response = httpx.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json={"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "response_format": {"type": "json_object"}}, timeout=45)
        response.raise_for_status()
        body = response.json()
        # Book the usage before parsing the content: a malformed response is still a billed call.
        self._ledger.record(usage_from_response(body, self.model))
        return json.loads(body["choices"][0]["message"]["content"])


def build_model_client(settings: Settings | None = None) -> ModelClient:
    settings = settings or Settings.from_env()
    if not settings.model_enabled:
        return MockClient()
    return DeepSeekClient(
        api_key=settings.api_key or "",
        base_url=settings.base_url,
        model=settings.model,
    )
