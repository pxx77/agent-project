from __future__ import annotations

from pydantic import BaseModel, Field, computed_field


class ColumnProfile(BaseModel):
    name: str
    type: str
    null_count: int = 0
    examples: list[str] = Field(default_factory=list)


class TableProfile(BaseModel):
    name: str
    row_count: int
    columns: list[ColumnProfile]


class Profile(BaseModel):
    tables: dict[str, TableProfile]


class DatasetHandle:
    def __init__(self, name: str, rows: list[dict]):
        self.name = name
        self.rows = rows


class QueryResult(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[object]] = Field(default_factory=list)
    row_count: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    policy_blocked: bool = False


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str


class ChartSpec(BaseModel):
    kind: str = "table"
    x: str | None = None
    y: str | None = None


class Verification(BaseModel):
    consistent: bool
    reason: str


class TokenUsage(BaseModel):
    """Tokens a run consumed, tagged with whether they were measured or estimated.

    A live call fills this from the provider's ``usage`` field and leaves ``measured`` True. The
    offline replay has no provider to ask, so it fills the same fields from a local estimate and
    sets ``measured`` False. Carrying the flag on the record itself means a reader can never
    mistake an estimate for a measurement.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    calls: int = 0
    measured: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        """Prompt plus completion, so a consumer does not have to know to add them."""
        return self.prompt_tokens + self.completion_tokens


class Trace(BaseModel):
    status: str = "success"
    states: list[str] = Field(default_factory=list)
    retries: int = 0
    duration_ms: float = 0.0
    model_calls: int = 0
    provider: str = "mock"
    model: str = "deterministic-fixture"
    usage: TokenUsage = Field(default_factory=TokenUsage)
    #: The model whose published rates produced ``cost_cny``; empty when nothing was billed.
    billing_model: str = ""
    #: Cost of ``usage`` in CNY, or ``None`` when the billing model is not in the price table.
    cost_cny: float | None = None


class AgentResult(BaseModel):
    sql: str
    query_result: QueryResult
    chart: ChartSpec | None = None
    conclusion: str
    verification: Verification
    trace: Trace
