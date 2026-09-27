"""Token 记账与成本核算。

两个来源分开对待，绝不混用：

实测
    真实调用 DeepSeek 时，token 数直接取自响应体的 ``usage`` 字段
    （``prompt_tokens`` / ``completion_tokens`` / ``prompt_cache_hit_tokens`` /
    ``prompt_cache_miss_tokens``），此时 :attr:`TokenUsage.measured` 为 True，费用是按
    实际用量和官方单价算出来的。

估算
    离线回放（``MockClient`` 与评测里的脚本化客户端）不产生任何真实计费，token 数按官方
    文档给出的换算比例在本机估算，:attr:`TokenUsage.measured` 为 False。费用只能作为量级
    参考。

价格表来自 DeepSeek 官方定价页（见 :data:`PRICE_SOURCE`），单位是「元 / 百万 tokens」。
官方说明 ``deepseek-chat`` 与 ``deepseek-reasoner`` 将于北京时间 2026/07/24 23:59 弃用，
出于兼容二者分别对应 ``deepseek-v4-flash`` 的非思考与思考模式，因此这里按 flash 计价。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import TokenUsage

#: 价格出处与核对日期，避免价格引用变成无源之数。
PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing"
PRICE_VERIFIED_ON = "2026-09-28"
PRICE_CURRENCY = "CNY"
PRICE_UNIT = "per_million_tokens"

#: 官方文档给出的换算比例：1 个英文字符 ≈ 0.3 token，1 个中文字符 ≈ 0.6 token。
ENGLISH_CHARS_PER_TOKEN = 0.3
CJK_CHARS_PER_TOKEN = 0.6

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


@dataclass(frozen=True)
class ModelPrice:
    """单价，单位为「元 / 百万 tokens」。"""

    cache_hit_input: float
    cache_miss_input: float
    output: float


PRICE_TABLE: dict[str, ModelPrice] = {
    "deepseek-v4-flash": ModelPrice(cache_hit_input=0.02, cache_miss_input=1.0, output=2.0),
    "deepseek-v4-pro": ModelPrice(cache_hit_input=0.025, cache_miss_input=3.0, output=6.0),
}
# 弃用中的旧模型名按官方兼容说明映射到 flash。
PRICE_TABLE["deepseek-chat"] = PRICE_TABLE["deepseek-v4-flash"]
PRICE_TABLE["deepseek-reasoner"] = PRICE_TABLE["deepseek-v4-flash"]


def price_for(model: str) -> ModelPrice | None:
    """Return the official price, or ``None`` when the model is not in the table.

    ``None`` rather than a stand-in rate: quoting another model's price for an unknown
    one would put a fabricated number in the report.
    """
    return PRICE_TABLE.get(model)


def cost_of(usage: TokenUsage, model: str) -> float | None:
    """Cost in CNY for ``usage`` at ``model``'s published rates, or ``None`` if unpriced."""
    price = price_for(model)
    if price is None:
        return None
    return (
        usage.cache_hit_tokens * price.cache_hit_input
        + usage.cache_miss_tokens * price.cache_miss_input
        + usage.completion_tokens * price.output
    ) / 1_000_000


def merge_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    """Sum two usage records.

    ``measured`` is the conjunction: a total is only fully measured when every call it
    sums was measured, so one estimated call is enough to mark the whole as estimated.
    """
    return TokenUsage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        cache_hit_tokens=left.cache_hit_tokens + right.cache_hit_tokens,
        cache_miss_tokens=left.cache_miss_tokens + right.cache_miss_tokens,
        calls=left.calls + right.calls,
        measured=left.measured and right.measured,
    )


def estimate_tokens(text: str) -> int:
    """Approximate a token count from the character ratios in the official docs.

    Only for the offline path. A real call returns exact counts in ``usage``, and this
    estimator exists so offline runs can still exercise the accounting without the result
    being mistaken for a measurement.
    """
    cjk_chars = len(_CJK.findall(text))
    other_chars = len(text) - cjk_chars
    return max(0, math.ceil(cjk_chars * CJK_CHARS_PER_TOKEN + other_chars * ENGLISH_CHARS_PER_TOKEN))


def estimated_usage(prompt: str, completion: str) -> TokenUsage:
    """Build an estimated usage record for one offline call.

    The cache cannot be observed offline, so every prompt token is booked as a cache miss.
    That books the *undiscounted* rate, which makes the resulting figure an upper bound
    instead of a guess at a discount the caller never earned.
    """
    prompt_tokens = estimate_tokens(prompt)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=estimate_tokens(completion),
        cache_hit_tokens=0,
        cache_miss_tokens=prompt_tokens,
        calls=1,
        measured=False,
    )


def usage_from_response(payload: dict, model: str) -> TokenUsage:
    """Read a chat-completions response body into a measured usage record."""
    usage = payload.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    cache_miss = usage.get("prompt_cache_miss_tokens")
    if cache_miss is None:
        # A provider that does not report the cache split is assumed to have missed
        # entirely, which is the conservative reading of the same token count.
        cache_miss = max(0, prompt_tokens - cache_hit)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=int(cache_miss),
        calls=1,
        measured=True,
    )


class UsageLedger:
    """Collects per-call usage and hands it out as a drainable delta.

    Draining rather than totalling matters because the evaluation reuses one client across
    every case: a lifetime counter would report the whole run's tokens on the first case
    and double-count from then on.
    """

    def __init__(self) -> None:
        self._pending = TokenUsage()

    def record(self, usage: TokenUsage) -> None:
        self._pending = merge_usage(self._pending, usage)

    def drain(self) -> TokenUsage:
        pending, self._pending = self._pending, TokenUsage()
        return pending


def drain_usage(client: object) -> TokenUsage:
    """Read and reset a client's pending usage, tolerating clients that do not track it."""
    consume = getattr(client, "consume_usage", None)
    if consume is None:
        return TokenUsage()
    return consume()
