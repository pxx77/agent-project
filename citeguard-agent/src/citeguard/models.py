from __future__ import annotations

from pydantic import BaseModel, Field, computed_field


class Document(BaseModel):
    id: str
    name: str
    text: str


class Chunk(BaseModel):
    id: str
    document_id: str
    text: str
    start: int
    end: int


class Claim(BaseModel):
    text: str
    citations: list[str] = Field(default_factory=list)
    status: str = "uncertain"


class Verification(BaseModel):
    supported_claims: int = 0
    unsupported_claims: int = 0
    support_rate: float = 0.0


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
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retriever: str = "bm25"
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
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    evidence: list[Chunk] = Field(default_factory=list)
    verification: Verification
    trace: Trace
