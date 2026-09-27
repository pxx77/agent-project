from __future__ import annotations

from pydantic import BaseModel, Field


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


class Trace(BaseModel):
    status: str = "success"
    states: list[str] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retries: int = 0
    duration_ms: float = 0.0
    model_calls: int = 0
    provider: str = "mock"
    model: str = "deterministic-fixture"


class AgentResult(BaseModel):
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    evidence: list[Chunk] = Field(default_factory=list)
    verification: Verification
    trace: Trace
