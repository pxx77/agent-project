from __future__ import annotations

from pydantic import BaseModel, Field


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


class Trace(BaseModel):
    status: str = "success"
    states: list[str] = Field(default_factory=list)
    retries: int = 0
    duration_ms: float = 0.0
    model_calls: int = 0


class AgentResult(BaseModel):
    sql: str
    query_result: QueryResult
    chart: ChartSpec | None = None
    conclusion: str
    verification: Verification
    trace: Trace
