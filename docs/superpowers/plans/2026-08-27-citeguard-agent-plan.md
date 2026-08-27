# CiteGuard Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-day, locally runnable document research Agent that answers with resolvable citations and a verifier score.

**Architecture:** A FastAPI service and Streamlit UI call a bounded workflow: ingest -> index -> plan -> retrieve -> answer -> verify. The default mock model produces deterministic outputs for tests; an OpenAI-compatible adapter enables DeepSeek through environment variables. BM25 is the offline retrieval baseline; optional embeddings are an enhancement, not a requirement.

**Tech Stack:** Python 3.11+, FastAPI, Streamlit, Pydantic, rank-bm25, pypdf, python-docx, httpx, pytest, OpenAI-compatible API.

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
- No external account, cloud database, or Docker daemon is required for local use.

---

### Task 1: Scaffold and configuration

**Files:**
- Create: `citeguard-agent/pyproject.toml`
- Create: `citeguard-agent/.env.example`
- Create: `citeguard-agent/.gitignore`
- Create: `citeguard-agent/README.md`
- Create: `citeguard-agent/src/citeguard/__init__.py`
- Create: `citeguard-agent/src/citeguard/config.py`
- Create: `citeguard-agent/tests/test_config.py`

**Interfaces:**
- `Settings.from_env() -> Settings` reads `CITEGUARD_MOCK`, `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`, `MAX_RETRIES`, and `MAX_FILE_MB`.
- `Settings.model_enabled -> bool` is false when mock mode is enabled or the key is absent.

- [ ] **Step 1: Write the failing test**

```python
def test_mock_mode_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("CITEGUARD_MOCK", "1")
    from citeguard.config import Settings
    settings = Settings.from_env()
    assert settings.mock is True
    assert settings.model_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL because `citeguard` and `Settings` do not exist.

- [ ] **Step 3: Write minimal implementation**

Create a typed `Settings` dataclass with safe defaults, parse booleans and integers, and never print the API key. Pin runtime dependencies in `pyproject.toml`; set the package source root to `src`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add citeguard-agent
git commit -m "feat(citeguard): scaffold configuration"
```

### Task 2: Document ingestion and citation-safe indexing

**Files:**
- Create: `citeguard-agent/src/citeguard/models.py`
- Create: `citeguard-agent/src/citeguard/ingestion.py`
- Create: `citeguard-agent/src/citeguard/retrieval.py`
- Create: `citeguard-agent/tests/test_ingestion.py`
- Create: `citeguard-agent/tests/test_retrieval.py`
- Create: `citeguard-agent/fixtures/ai_agents.md`

**Interfaces:**
- `Document(id: str, name: str, text: str)` and `Chunk(id: str, document_id: str, text: str, start: int, end: int)` are Pydantic models.
- `parse_bytes(name: str, data: bytes) -> Document` supports `.txt`, `.md`, `.pdf`, and `.docx`.
- `chunk_document(document: Document, size: int = 700, overlap: int = 100) -> list[Chunk]` produces stable IDs `doc_id:chunk_index`.
- `BM25Index(chunks: list[Chunk]).search(query: str, limit: int = 5) -> list[Chunk]` returns deterministic ranked chunks.

- [ ] **Step 1: Write the failing tests**

```python
def test_chunk_ids_are_stable():
    from citeguard.ingestion import chunk_document
    from citeguard.models import Document
    chunks = chunk_document(Document(id="d1", name="x.md", text="alpha " * 300), size=40, overlap=5)
    assert chunks[0].id == "d1:0"
    assert chunks[1].document_id == "d1"

def test_bm25_returns_evidence_for_query():
    from citeguard.models import Chunk
    from citeguard.retrieval import BM25Index
    index = BM25Index([Chunk(id="d:0", document_id="d", text="retrieval augmented generation", start=0, end=32)])
    assert index.search("retrieval", 1)[0].id == "d:0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ingestion.py tests/test_retrieval.py -v`
Expected: FAIL with missing models and functions.

- [ ] **Step 3: Write minimal implementation**

Normalize Unicode whitespace, reject files above `MAX_FILE_MB`, use pypdf and python-docx only for extraction, and preserve source offsets. Tokenize BM25 with lowercase alphanumeric terms. Return an empty list for an empty query.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ingestion.py tests/test_retrieval.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add citeguard-agent/src/citeguard/models.py citeguard-agent/src/citeguard/ingestion.py citeguard-agent/src/citeguard/retrieval.py citeguard-agent/tests citeguard-agent/fixtures
git commit -m "feat(citeguard): add document ingestion and retrieval"
```

### Task 3: Bounded Agent workflow and DeepSeek adapter

**Files:**
- Create: `citeguard-agent/src/citeguard/llm.py`
- Create: `citeguard-agent/src/citeguard/workflow.py`
- Create: `citeguard-agent/src/citeguard/validation.py`
- Create: `citeguard-agent/tests/test_workflow.py`

**Interfaces:**
- `ModelClient.complete_json(system: str, user: str, schema_name: str) -> dict` is implemented by `MockClient` and `DeepSeekClient`.
- `run_agent(question: str, index: BM25Index, client: ModelClient) -> AgentResult` performs at most `settings.max_retries` repairs.
- `AgentResult.answer: str`, `claims: list[Claim]`, `citations: list[str]`, `verification: Verification`, `trace: Trace`.

- [ ] **Step 1: Write the failing tests**

```python
def test_mock_workflow_returns_resolvable_citation():
    result = build_fixture_agent().run("What does the fixture say about evaluation?")
    assert result.citations
    assert all(c in result.trace.retrieved_chunk_ids for c in result.citations)
    assert result.verification.supported_claims >= 1

def test_empty_retrieval_is_explicit():
    result = build_fixture_agent().run("an unrelated phrase xyz")
    assert "insufficient" in result.answer.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_workflow.py -v`
Expected: FAIL because the workflow and clients do not exist.

- [ ] **Step 3: Write minimal implementation**

Use Pydantic schemas for plan, claims, citations, and verification. The mock client maps fixture keywords to chunk IDs. The DeepSeek client uses `httpx` against `DEEPSEEK_BASE_URL`, sends no key in logs, and validates JSON. Record state transitions and elapsed milliseconds. On malformed model output, retry once with the validation error; on empty retrieval, return a no-evidence result without inventing citations.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_workflow.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add citeguard-agent/src/citeguard/llm.py citeguard-agent/src/citeguard/workflow.py citeguard-agent/src/citeguard/validation.py citeguard-agent/tests/test_workflow.py
git commit -m "feat(citeguard): add verified research workflow"
```

### Task 4: API, Streamlit UI, and MCP-compatible tools

**Files:**
- Create: `citeguard-agent/src/citeguard/api.py`
- Create: `citeguard-agent/src/citeguard/ui.py`
- Create: `citeguard-agent/src/citeguard/mcp_server.py`
- Create: `citeguard-agent/tests/test_api.py`

**Interfaces:**
- `GET /health -> {"status": "ok", "mock": bool}`.
- `POST /ingest` accepts multipart files and returns `{"document_id": str, "chunks": int}`.
- `POST /ask` accepts `{"question": str}` and returns serialized `AgentResult`.
- MCP tools: `search_documents(query, limit)` and `get_chunk(chunk_id)` return JSON-safe dictionaries.

- [ ] **Step 1: Write the failing API tests**

```python
def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_ask_returns_trace(client):
    response = client.post("/ask", json={"question": "What is evaluation?"})
    assert response.status_code == 200
    assert response.json()["trace"]["status"] == "success"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py -v`
Expected: FAIL because the FastAPI app does not exist.

- [ ] **Step 3: Write minimal implementation**

Keep an in-memory index per process for the MVP. Limit upload size and return HTTP 400 for unsupported files. The Streamlit page provides file upload, question input, answer, evidence, and expandable trace sections. MCP server functions call the same index methods and do not create external side effects.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add citeguard-agent/src/citeguard/api.py citeguard-agent/src/citeguard/ui.py citeguard-agent/src/citeguard/mcp_server.py citeguard-agent/tests/test_api.py
git commit -m "feat(citeguard): expose api ui and tools"
```

### Task 5: Evaluation, smoke test, and portfolio documentation

**Files:**
- Create: `citeguard-agent/evals/cases.json`
- Create: `citeguard-agent/evals/run_eval.py`
- Create: `citeguard-agent/scripts/smoke.py`
- Modify: `citeguard-agent/README.md`
- Create: `citeguard-agent/Dockerfile`

**Interfaces:**
- `python evals/run_eval.py --mock --output evals/report.json` writes per-case results and aggregate `citation_resolvability`, `support_rate`, `success_rate`, and `avg_latency_ms`.
- `python scripts/smoke.py` exits 0 after one fixture question succeeds.

- [ ] **Step 1: Write evaluation fixtures and assertions**

Include twenty questions: ten answerable, five requiring multiple citations, and five deliberately unsupported. Assert that unsupported cases never receive a citation and that answerable cases resolve every citation.

- [ ] **Step 2: Run evaluation to verify the initial report is meaningful**

Run: `python evals/run_eval.py --mock --output evals/report.json`
Expected: JSON report exists and includes all 20 cases, with unsupported cases listed separately.

- [ ] **Step 3: Add README, Dockerfile, and smoke command**

Document `CITEGUARD_MOCK=1 streamlit run src/citeguard/ui.py`, `uvicorn citeguard.api:app`, DeepSeek environment setup without any secret value, architecture Mermaid diagram, measured metrics table, and a truthful limitation that Docker is unverified when Docker is unavailable.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest -q`; `python scripts/smoke.py`; `git grep -n -E 'sk-[A-Za-z0-9]{20,}' -- . ':!*.docx'`
Expected: all tests pass, smoke exits 0, and secret scan returns no matches.

- [ ] **Step 5: Commit**

```bash
git add citeguard-agent
git commit -m "feat(citeguard): add evaluation and portfolio docs"
```
