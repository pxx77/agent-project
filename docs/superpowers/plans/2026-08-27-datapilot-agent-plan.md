# DataPilot Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-day, locally runnable data-analysis Agent that generates safe SQL, repairs execution errors, and verifies conclusions against query results.

**Architecture:** A FastAPI service and Streamlit UI call a bounded workflow: profile -> plan -> generate SQL -> validate policy -> execute in DuckDB -> repair -> chart -> verify. The default mock model handles fixture questions deterministically; an OpenAI-compatible adapter enables DeepSeek via environment variables. No generated Python is executed.

**Tech Stack:** Python 3.11+, FastAPI, Streamlit, Pydantic, DuckDB, pandas, polars, sqlglot, plotly, httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-08-27-agent-portfolio-design.md`

## Global Constraints

- Python 3.11 or newer.
- Streamlit provides the user interface; no separate JavaScript frontend.
- FastAPI exposes health and Agent execution APIs.
- The model client uses `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` environment variables.
- `.env` is gitignored. Only `.env.example` is committed.
- Mock mode is the default for tests and works without a model key.
- Agent execution is an explicit state graph with bounded retries, not an unconstrained multi-agent conversation.
- Each run records duration, model/tool calls, retry count, status, and evaluator scores.
- Generated SQL is read-only; generated Python is never executed.
- No external account, cloud database, or Docker daemon is required for local use.

---

### Task 1: Scaffold and configuration

**Files:**
- Create: `datapilot-agent/pyproject.toml`
- Create: `datapilot-agent/.env.example`
- Create: `datapilot-agent/.gitignore`
- Create: `datapilot-agent/README.md`
- Create: `datapilot-agent/src/datapilot/__init__.py`
- Create: `datapilot-agent/src/datapilot/config.py`
- Create: `datapilot-agent/tests/test_config.py`

**Interfaces:**
- `Settings.from_env() -> Settings` reads `DATAPILOT_MOCK`, `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`, `MAX_RETRIES`, and `MAX_ROWS`.
- `Settings.model_enabled -> bool` is false when mock mode is enabled or the key is absent.

- [ ] **Step 1: Write the failing test**

```python
def test_datapilot_defaults_to_mock_without_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DATAPILOT_MOCK", "1")
    from datapilot.config import Settings
    settings = Settings.from_env()
    assert settings.mock is True
    assert settings.model_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL because `datapilot` and `Settings` do not exist.

- [ ] **Step 3: Write minimal implementation**

Create a typed settings dataclass with bounded integer limits and no secret logging. Pin dependencies in `pyproject.toml` and use the `src` package layout.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add datapilot-agent
git commit -m "feat(datapilot): scaffold configuration"
```

### Task 2: Data ingestion and profiling

**Files:**
- Create: `datapilot-agent/src/datapilot/models.py`
- Create: `datapilot-agent/src/datapilot/ingestion.py`
- Create: `datapilot-agent/src/datapilot/profiling.py`
- Create: `datapilot-agent/tests/test_ingestion.py`
- Create: `datapilot-agent/tests/test_profiling.py`
- Create: `datapilot-agent/fixtures/sales.csv`

**Interfaces:**
- `Dataset(name: str, tables: dict[str, str])` identifies registered DuckDB tables.
- `load_bytes(name: str, data: bytes) -> DatasetHandle` supports `.csv`, `.xlsx`, and `.sqlite`.
- `profile_dataset(handle: DatasetHandle) -> Profile` returns table names, columns, types, null counts, row counts, and example values.

- [ ] **Step 1: Write failing tests**

```python
def test_fixture_profile_has_expected_columns():
    handle = load_fixture("sales.csv")
    profile = profile_dataset(handle)
    assert "region" in profile.tables["sales"].columns
    assert profile.tables["sales"].row_count == 12

def test_xlsx_and_csv_use_safe_table_names():
    handle = load_bytes("Q1 sales.xlsx", make_xlsx_bytes())
    assert list(handle.tables) == ["q1_sales"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ingestion.py tests/test_profiling.py -v`
Expected: FAIL because ingestion and profiling do not exist.

- [ ] **Step 3: Write minimal implementation**

Create a temporary local DuckDB connection, normalize names to lowercase snake case, cap imported rows at `MAX_ROWS`, and reject unsupported extensions. Use pandas only for input conversion; return Pydantic-safe profile objects.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ingestion.py tests/test_profiling.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add datapilot-agent/src/datapilot/models.py datapilot-agent/src/datapilot/ingestion.py datapilot-agent/src/datapilot/profiling.py datapilot-agent/tests datapilot-agent/fixtures
git commit -m "feat(datapilot): add dataset ingestion and profiling"
```

### Task 3: SQL policy and bounded execution

**Files:**
- Create: `datapilot-agent/src/datapilot/sql_policy.py`
- Create: `datapilot-agent/src/datapilot/execution.py`
- Create: `datapilot-agent/tests/test_sql_policy.py`
- Create: `datapilot-agent/tests/test_execution.py`

**Interfaces:**
- `validate_sql(sql: str) -> PolicyDecision(allowed: bool, reason: str)` rejects writes, external access, multiple statements, and disallowed functions.
- `execute_sql(handle: DatasetHandle, sql: str, max_rows: int) -> QueryResult` returns columns, rows, row_count, duration_ms, and error.

- [ ] **Step 1: Write failing tests**

```python
import pytest

@pytest.mark.parametrize("sql", ["DELETE FROM sales", "DROP TABLE sales", "ATTACH 'x' AS x", "SELECT 1; SELECT 2"])
def test_unsafe_sql_is_blocked(sql):
    from datapilot.sql_policy import validate_sql
    decision = validate_sql(sql)
    assert decision.allowed is False

def test_safe_aggregate_executes():
    result = execute_fixture("SELECT region, SUM(revenue) AS total FROM sales GROUP BY region")
    assert "total" in result.columns
    assert result.error is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sql_policy.py tests/test_execution.py -v`
Expected: FAIL because policy and execution functions do not exist.

- [ ] **Step 3: Write minimal implementation**

Use sqlglot to parse exactly one statement in DuckDB dialect, allow only SELECT/WITH, reject `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `ATTACH`, `COPY`, `INSTALL`, `LOAD`, `EXPORT`, `CREATE`, table functions, and filesystem/network references. Apply a row limit to non-aggregate queries and never expose raw connection objects.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sql_policy.py tests/test_execution.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add datapilot-agent/src/datapilot/sql_policy.py datapilot-agent/src/datapilot/execution.py datapilot-agent/tests
git commit -m "feat(datapilot): enforce safe sql execution"
```

### Task 4: Agent workflow and DeepSeek adapter

**Files:**
- Create: `datapilot-agent/src/datapilot/llm.py`
- Create: `datapilot-agent/src/datapilot/workflow.py`
- Create: `datapilot-agent/src/datapilot/visualization.py`
- Create: `datapilot-agent/tests/test_workflow.py`

**Interfaces:**
- `ModelClient.complete_json(system: str, user: str, schema_name: str) -> dict` is implemented by `MockClient` and `DeepSeekClient`.
- `run_agent(question: str, handle: DatasetHandle, client: ModelClient) -> AgentResult` retries safe SQL repair at most `settings.max_retries` times.
- `AgentResult.sql: str`, `query_result: QueryResult`, `chart: ChartSpec | None`, `conclusion: str`, `verification: Verification`, `trace: Trace`.

- [ ] **Step 1: Write failing tests**

```python
def test_mock_agent_answers_fixture_question():
    result = build_fixture_agent().run("What is revenue by region?")
    assert result.query_result.error is None
    assert "region" in result.query_result.columns
    assert result.verification.consistent is True

def test_agent_repairs_safe_sql_error_once():
    result = build_fixture_agent().run("Show revenue by region with a typo")
    assert result.trace.retries <= 2
    assert result.query_result.error is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_workflow.py -v`
Expected: FAIL because workflow and model clients do not exist.

- [ ] **Step 3: Write minimal implementation**

Use Pydantic schemas for intent, SQL, chart, and verification. The mock client maps fixture questions to fixed SQL. The DeepSeek client calls the configured endpoint, validates JSON, and never executes model-provided Python. On safe SQL errors, send the error text back for at most two repair attempts; policy violations fail immediately. The chart node emits only a safe Plotly spec using returned columns.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_workflow.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add datapilot-agent/src/datapilot/llm.py datapilot-agent/src/datapilot/workflow.py datapilot-agent/src/datapilot/visualization.py datapilot-agent/tests/test_workflow.py
git commit -m "feat(datapilot): add safe analysis workflow"
```

### Task 5: API, UI, MCP-compatible tools, and evaluation

**Files:**
- Create: `datapilot-agent/src/datapilot/api.py`
- Create: `datapilot-agent/src/datapilot/ui.py`
- Create: `datapilot-agent/src/datapilot/mcp_server.py`
- Create: `datapilot-agent/tests/test_api.py`
- Create: `datapilot-agent/evals/cases.json`
- Create: `datapilot-agent/evals/run_eval.py`
- Create: `datapilot-agent/scripts/smoke.py`
- Modify: `datapilot-agent/README.md`
- Create: `datapilot-agent/Dockerfile`

**Interfaces:**
- `GET /health -> {"status": "ok", "mock": bool}`.
- `POST /profile` accepts one multipart data file and returns the `Profile`.
- `POST /ask` accepts `{"question": str}` and returns serialized `AgentResult`.
- MCP tools: `get_schema()`, `run_safe_query(sql)`, and `make_chart_spec(columns, rows)` return JSON-safe dictionaries.
- `python evals/run_eval.py --mock --output evals/report.json` writes execution accuracy, repair success, unsafe-query blocking, average latency, and per-case results.

- [ ] **Step 1: Write failing API and eval assertions**

```python
def test_health(client):
    response = client.get("/health")
    assert response.json()["status"] == "ok"

def test_ask_returns_sql_and_trace(client):
    response = client.post("/ask", json={"question": "What is revenue by region?"})
    assert response.status_code == 200
    assert response.json()["trace"]["status"] == "success"
    assert response.json()["sql"].lower().startswith("select")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py -v`
Expected: FAIL because the API does not exist.

- [ ] **Step 3: Write minimal implementation**

Keep one in-memory fixture handle per process for the MVP. The Streamlit UI shows profile, question, generated SQL, policy decision, result table, chart, conclusion, and trace. Build twenty cases: ten aggregations, five filter/group tasks, and five unsafe SQL prompts that must be blocked. Add README commands, Mermaid architecture, resume bullets based on measured results, and Dockerfile syntax.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest -q`; `python scripts/smoke.py`; `python evals/run_eval.py --mock --output evals/report.json`; `git grep -n -E 'sk-[A-Za-z0-9]{20,}' -- . ':!*.docx'`
Expected: all tests pass, smoke exits 0, report contains 20 cases, and secret scan returns no matches.

- [ ] **Step 5: Commit**

```bash
git add datapilot-agent
git commit -m "feat(datapilot): add api ui evals and portfolio docs"
```
