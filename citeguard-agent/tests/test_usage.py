"""token 记账与成本核算的单元测试。

覆盖价格表、估算比例、账本增量语义，以及 workflow 把用量与费用写进 ``trace`` 的整条链路。
"""

import json

import pytest

from citeguard.config import Settings
from citeguard.llm import DeepSeekClient, MockClient
from citeguard.models import TokenUsage
from citeguard.usage import (
    PRICE_TABLE,
    UsageLedger,
    cost_of,
    drain_usage,
    estimate_tokens,
    estimated_usage,
    merge_usage,
    price_for,
    usage_from_response,
)
from citeguard.workflow import build_fixture_agent

QUESTION = "Which evaluation dimensions do reliable agents use?"


def test_deprecated_model_names_are_priced_at_flash_rates():
    """官方已公告 deepseek-chat / deepseek-reasoner 弃用并映射到 v4-flash，价格必须跟着走。"""
    assert PRICE_TABLE["deepseek-chat"] is PRICE_TABLE["deepseek-v4-flash"]
    assert PRICE_TABLE["deepseek-reasoner"] is PRICE_TABLE["deepseek-v4-flash"]


def test_unknown_model_has_no_price_instead_of_a_stand_in_rate():
    assert price_for("some-other-vendor-model") is None
    # None 而不是 0：0 会让「没有价格」看起来像「免费」。
    assert cost_of(TokenUsage(prompt_tokens=1000), "some-other-vendor-model") is None


def test_cost_sums_the_three_published_rates():
    usage = TokenUsage(
        prompt_tokens=2_000_000,
        completion_tokens=1_000_000,
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
    )
    # flash：命中输入 0.02 + 未命中输入 1.0 + 输出 2.0 = 3.02 元。
    assert cost_of(usage, "deepseek-chat") == pytest.approx(3.02)
    # pro：0.025 + 3.0 + 6.0 = 9.025 元，说明取价确实按模型走，而不是一个常数。
    assert cost_of(usage, "deepseek-v4-pro") == pytest.approx(9.025)


def test_estimate_tokens_treats_cjk_as_denser_than_latin():
    assert estimate_tokens("一二三四五六七八九十") == 6  # 10 * 0.6
    assert estimate_tokens("abcdefghij") == 3  # 10 * 0.3
    assert estimate_tokens("") == 0


def test_offline_estimate_books_the_undiscounted_rate():
    usage = estimated_usage("hello", "world")

    assert usage.measured is False
    assert usage.cache_hit_tokens == 0
    # 离线观察不到缓存命中，因此全部按未命中计，得到的是上界而不是乐观折扣。
    assert usage.cache_miss_tokens == usage.prompt_tokens
    assert usage.calls == 1


def test_measured_usage_reads_the_provider_fields():
    usage = usage_from_response(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_cache_hit_tokens": 40,
                "prompt_cache_miss_tokens": 60,
            }
        },
        "deepseek-chat",
    )

    assert usage.measured is True
    assert (usage.prompt_tokens, usage.completion_tokens) == (100, 20)
    assert (usage.cache_hit_tokens, usage.cache_miss_tokens) == (40, 60)
    assert usage.total_tokens == 120


def test_missing_cache_split_is_read_as_a_full_miss():
    """服务商没报缓存拆分时按全部未命中算，这是同一份 token 数里更保守的读法。"""
    usage = usage_from_response({"usage": {"prompt_tokens": 100, "completion_tokens": 5}}, "deepseek-chat")

    assert usage.cache_hit_tokens == 0
    assert usage.cache_miss_tokens == 100


def test_merge_usage_is_conjunctive_for_measured():
    measured = usage_from_response({"usage": {"prompt_tokens": 10, "completion_tokens": 1}}, "deepseek-chat")

    assert merge_usage(measured, measured).measured is True
    # 一次估算就足以把合计标成估算，不让估算混进实测里冒充实测。
    assert merge_usage(measured, estimated_usage("a", "b")).measured is False


def test_ledger_returns_a_delta_and_resets():
    ledger = UsageLedger()
    ledger.record(estimated_usage("a", "b"))
    ledger.record(estimated_usage("a", "b"))

    first = ledger.drain()

    assert first.calls == 2
    assert first.prompt_tokens > 0
    # 再取一次必须是空的：账本是增量而不是累计，否则复用同一个客户端的评测会重复计数。
    second = ledger.drain()
    assert second.calls == 0
    assert second.total_tokens == 0


def test_drain_usage_tolerates_a_client_that_does_not_track_usage():
    class Bare:
        provider = "bare"
        model = "bare"

        def complete_json(self, system: str, user: str, schema_name: str) -> dict:
            return {}

    assert drain_usage(Bare()).total_tokens == 0
    assert drain_usage(object()).calls == 0


def test_trace_carries_tokens_and_cost_for_every_run():
    client = MockClient()
    agent = build_fixture_agent()
    expected_model = Settings.from_env().model

    first = agent.run(QUESTION, client)
    second = agent.run(QUESTION, client)

    for result in (first, second):
        # 一次回答 + 一次独立的claim核验，账本上的调用数必须和 trace 自己的计数一致。
        assert result.trace.usage.calls == result.trace.model_calls == 2
        assert result.trace.usage.total_tokens > 0
        assert result.trace.usage.measured is False
        # 离线回放按配置模型计价，而不是按客户端自己的 fixture 名。
        assert result.trace.billing_model == expected_model
        assert result.trace.cost_cny == pytest.approx(cost_of(result.trace.usage, expected_model))

    # 同一个客户端连跑两次，第二次拿到的仍是本次的量，说明上一轮的账本确实被排空了。
    assert second.trace.usage.total_tokens == first.trace.usage.total_tokens


def test_no_evidence_run_reports_no_usage_and_no_cost():
    result = build_fixture_agent().run("unrelated phrase xyz")

    assert result.trace.status == "insufficient_evidence"
    assert result.trace.usage.total_tokens == 0
    assert result.trace.cost_cny is None
    assert result.trace.billing_model == ""


def test_live_client_books_measured_usage(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": '{"answer": "ok"}'}}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "prompt_cache_hit_tokens": 400,
                    "prompt_cache_miss_tokens": 600,
                },
            }

    monkeypatch.setattr("citeguard.llm.httpx.post", lambda *args, **kwargs: Response())
    client = DeepSeekClient(api_key="k", base_url="https://api.deepseek.com", model="deepseek-chat")

    client.complete_json("system", "user", "citation_answer")
    usage = client.consume_usage()

    assert usage.measured is True
    assert usage.calls == 1
    assert (usage.cache_hit_tokens, usage.cache_miss_tokens) == (400, 600)
    # 已经取走的量不会再次出现。
    assert client.consume_usage().calls == 0


def test_live_run_is_priced_at_the_model_it_called(monkeypatch):
    monkeypatch.setenv("CITEGUARD_MOCK", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    body = {
        "answer": "智能体评测需要检查任务成功率和引用证据。",
        "claims": [{"text": "智能体评测需要检查任务成功率和引用证据。", "citations": ["cn:0"]}],
        "supported": True,
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 2000, "completion_tokens": 200},
            }

    monkeypatch.setattr("citeguard.llm.httpx.post", lambda *args, **kwargs: Response())

    from citeguard.ingestion import chunk_document
    from citeguard.models import Document
    from citeguard.retrieval import BM25Index
    from citeguard.workflow import run_agent

    index = BM25Index(chunk_document(Document(id="cn", name="guide.md", text="智能体评测需要检查任务成功率和引用证据。")))
    result = run_agent("智能体评测有哪些要求？", index)

    assert result.trace.provider == "deepseek"
    assert result.trace.usage.measured is True
    # 线上运行按它真正调用的模型计价，而不是按配置里的默认模型。
    assert result.trace.billing_model == "deepseek-v4-pro"
    assert result.trace.cost_cny == pytest.approx(cost_of(result.trace.usage, "deepseek-v4-pro"))
    assert result.trace.cost_cny > 0
