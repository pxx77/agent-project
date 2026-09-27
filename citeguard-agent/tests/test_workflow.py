import json

import pytest

from citeguard.ingestion import chunk_document
from citeguard.models import Document
from citeguard.retrieval import BM25Index
from citeguard.workflow import build_fixture_agent


def test_mock_workflow_returns_resolvable_citation():
    result = build_fixture_agent().run("Which evaluation dimensions do reliable agents use?")
    assert result.citations
    assert all(c in result.trace.retrieved_chunk_ids for c in result.citations)
    assert result.verification.supported_claims >= 1


def test_builtin_fixture_supports_the_default_chinese_question(monkeypatch):
    monkeypatch.setenv("CITEGUARD_MOCK", "1")

    result = build_fixture_agent().run("文档对 Agent 评测提出了哪些要求？")

    assert result.citations
    # One call produces the answer, the second independently judges the claim against its evidence.
    assert result.trace.model_calls == 2


def test_empty_retrieval_is_explicit():
    result = build_fixture_agent().run("unrelated phrase xyz")
    assert "insufficient" in result.answer.lower()


def test_workflow_records_model_provider(monkeypatch):
    monkeypatch.setenv("CITEGUARD_MOCK", "1")
    result = build_fixture_agent().run("Which evaluation dimensions do reliable agents use?")
    assert result.trace.provider == "mock"
    assert result.trace.model


def test_no_evidence_reports_that_model_was_not_called(monkeypatch):
    monkeypatch.setenv("CITEGUARD_MOCK", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    result = build_fixture_agent().run("完全无关的量子烹饪问题")

    assert result.trace.provider == "not_called"
    assert result.trace.model_calls == 0


def test_missing_answer_from_model_is_an_explicit_error():
    class InvalidClient:
        provider = "deepseek"
        model = "deepseek-test-model"

        def complete_json(self, system: str, user: str, schema_name: str) -> dict:
            return {}

    agent = build_fixture_agent()

    with pytest.raises(ValueError, match="answer"):
        from citeguard.workflow import run_agent

        run_agent("Which evaluation dimensions do reliable agents use?", agent.index, InvalidClient())


def test_real_mode_posts_to_deepseek_for_chinese_evidence(monkeypatch):
    monkeypatch.setenv("CITEGUARD_MOCK", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test-model")
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"answer":"需要检查任务成功率和引用证据。","supported":true}'}}]}

    def fake_post(url, headers, json, timeout):
        requests.append({"url": url, "headers": headers, "body": json})
        return Response()

    monkeypatch.setattr("citeguard.llm.httpx.post", fake_post)
    document = Document(id="cn", name="guide.md", text="智能体评测需要检查任务成功率和引用证据。")
    index = BM25Index(chunk_document(document))

    from citeguard.workflow import run_agent

    result = run_agent("智能体评测有哪些要求？", index)

    assert len(requests) == 2
    assert requests[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert requests[0]["headers"]["Authorization"] == "Bearer test-key"
    assert requests[0]["body"]["model"] == "deepseek-test-model"
    assert result.trace.provider == "deepseek"
    assert result.trace.model_calls == 2
    assert result.verification.supported_claims == 1
    # The second request is an independent judgement carrying the claim and its cited evidence.
    verify_payload = json.loads(requests[1]["body"]["messages"][1]["content"])
    assert verify_payload["claim"] == "需要检查任务成功率和引用证据。"
    assert verify_payload["evidence"][0]["id"] == "cn:0"


def test_verification_rejects_a_claim_the_evidence_does_not_support():
    class ClaimingClient:
        provider = "stub"
        model = "stub-model"

        def complete_json(self, system: str, user: str, schema_name: str) -> dict:
            if schema_name == "claim_verification":
                return {"supported": False, "reason": "the evidence never mentions 1999"}
            return {
                "answer": "The project launched in 1999.",
                "claims": [{"text": "The project launched in 1999.", "citations": ["fixture:0"]}],
            }

    agent = build_fixture_agent()

    from citeguard.workflow import run_agent

    result = run_agent("Which evaluation dimensions do reliable agents use?", agent.index, ClaimingClient())

    assert result.verification.supported_claims == 0
    assert result.verification.unsupported_claims == 1
    assert result.verification.support_rate == 0.0
    assert result.claims[0].status == "unsupported"
    assert result.trace.status == "unsupported"


def test_support_rate_is_computed_from_mixed_claim_verdicts():
    class MixedClient:
        provider = "stub"
        model = "stub-model"

        def complete_json(self, system: str, user: str, schema_name: str) -> dict:
            if schema_name == "claim_verification":
                claim = json.loads(user)["claim"]
                return {"supported": claim.startswith("Supported")}
            return {
                "answer": "two claims",
                "claims": [
                    {"text": "Supported claim about evaluation.", "citations": ["fixture:0"]},
                    {"text": "Unsupported claim about 1999.", "citations": ["fixture:0"]},
                ],
            }

    agent = build_fixture_agent()

    from citeguard.workflow import run_agent

    result = run_agent("Which evaluation dimensions do reliable agents use?", agent.index, MixedClient())

    assert result.verification.supported_claims == 1
    assert result.verification.unsupported_claims == 1
    assert result.verification.support_rate == 0.5
    assert result.trace.status == "partial_support"


def test_unresolvable_citation_is_unsupported_without_calling_the_verifier():
    class DanglingCitationClient:
        provider = "stub"
        model = "stub-model"

        def complete_json(self, system: str, user: str, schema_name: str) -> dict:
            if schema_name == "claim_verification":
                raise AssertionError("the verifier must not be called for an unresolvable citation")
            return {"answer": "ok", "claims": [{"text": "ok", "citations": ["missing:42"]}]}

    agent = build_fixture_agent()

    from citeguard.workflow import run_agent

    result = run_agent("Which evaluation dimensions do reliable agents use?", agent.index, DanglingCitationClient())

    assert result.verification.unsupported_claims == 1
    assert result.verification.support_rate == 0.0
    assert result.trace.model_calls == 1


def test_offline_verification_flags_a_fabricated_claim():
    from citeguard.llm import MockClient

    class FabricatingClient:
        provider = "mock"
        model = "deterministic-fixture"

        def __init__(self) -> None:
            self._judge = MockClient()

        def complete_json(self, system: str, user: str, schema_name: str) -> dict:
            if schema_name == "claim_verification":
                return self._judge.complete_json(system, user, schema_name)
            claim = "The fixture was published in 1999."
            return {"answer": claim, "claims": [{"text": claim, "citations": ["fixture:0"]}]}

    agent = build_fixture_agent()

    from citeguard.workflow import run_agent

    result = run_agent("Which evaluation dimensions do reliable agents use?", agent.index, FabricatingClient())

    assert result.verification.supported_claims == 0
    assert result.verification.unsupported_claims == 1
    assert result.verification.support_rate == 0.0
    assert result.trace.status == "unsupported"
