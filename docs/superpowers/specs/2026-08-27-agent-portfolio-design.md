# One-Day Agent Portfolio Design

Date: 2026-08-27

> 实现现状（2026-09-27 更新）：本文件记录当天的设计意图，部分技术选型在实现时被替换（DataPilot 由 DuckDB 改为内存 SQLite，CiteGuard 的检索改为自行实现的 Okapi BM25）。两个项目当前的能力与评测数字分别以各自的 `README.md` 为准。

## Objective

Build two independent, demonstrable Agent MVPs in one working day for graduate applications and mainland China Agent-development internship screening. Both projects must run locally, use a configurable DeepSeek OpenAI-compatible endpoint, include deterministic mock mode, expose measurable evaluation results, and avoid storing credentials.

The deliverable is an honest MVP portfolio, not a production deployment or a claim of unaided authorship.

## Repository Layout

```text
agent-portfolio/
  citeguard-agent/
  datapilot-agent/
  docs/superpowers/specs/
```

The projects do not import application code from each other. They use the same environment-variable names and trace schema so they can be demonstrated consistently and split into separate repositories later.

## Shared Constraints

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

## Project 1: CiteGuard Agent

### User Flow

1. Upload PDF, DOCX, Markdown, or TXT files.
2. The ingestion service extracts text, assigns stable document and chunk identifiers, and builds a local index.
3. The user asks a question.
4. The planner rewrites the query and selects retrieval tools.
5. Hybrid retrieval combines BM25 and vector similarity when embeddings are configured; BM25 remains a complete offline fallback.
6. The answer node returns structured claims with chunk citations.
7. The verifier marks each claim as supported, unsupported, or uncertain.
8. The UI displays the answer, evidence excerpts, confidence, trace, and timing.

### Components

- `ingestion`: safe file parsing, normalization, metadata, and chunking.
- `index`: BM25 index plus optional local vector index.
- `tools`: document search and chunk lookup interfaces, also exposed through an MCP server.
- `workflow`: plan, retrieve, answer, verify, and bounded repair states.
- `evaluation`: fixture corpus, twenty questions, citation and support metrics.
- `api` and `ui`: FastAPI routes and Streamlit pages.

### Acceptance Criteria

- Ingest the bundled fixture documents and answer questions in mock mode.
- Every non-empty factual answer contains at least one resolvable citation.
- Unsupported mock claims are flagged by the verifier.
- Evaluation produces JSON and Markdown reports.
- Unit tests cover parsing, chunk IDs, retrieval, citation validation, retry limits, and API health.

## Project 2: DataPilot Agent

### User Flow

1. Upload CSV, XLSX, or SQLite data, or load the bundled fixture dataset.
2. The profiler reports tables, columns, types, nulls, and example values.
3. The user asks a natural-language analysis question.
4. The planner produces a structured analysis intent.
5. The SQL node generates a DuckDB query.
6. The policy layer parses SQL and rejects writes, external access, multiple statements, and disallowed functions.
7. DuckDB executes the query in a read-only workflow.
8. On a safe execution error, the repair node receives the error and retries at most twice.
9. The visualization node selects a constrained Plotly chart specification.
10. The verifier checks that the written conclusion matches returned columns and values.

### Components

- `ingestion`: CSV/XLSX/SQLite import into a temporary DuckDB database.
- `profiling`: schemas, summary statistics, and categorical samples.
- `tools`: schema inspection, safe SQL execution, and chart specification, also exposed through MCP.
- `workflow`: plan, SQL generation, policy validation, execution, repair, visualization, and verification.
- `evaluation`: twenty fixture questions with expected scalar, row-set, or aggregation results.
- `api` and `ui`: FastAPI routes and Streamlit pages.

### Acceptance Criteria

- Complete the bundled fixture tasks in mock mode.
- Reject INSERT, UPDATE, DELETE, DROP, ATTACH, COPY, INSTALL, LOAD, and multiple statements.
- Produce a table, constrained chart specification, conclusion, trace, and timing.
- Evaluation reports execution accuracy, repair success, and unsafe-query blocking.
- Unit tests cover ingestion, schema profiling, SQL policy, execution, retry limits, and API health.

## Model Integration

The production adapter uses the OpenAI Python client against a configurable DeepSeek-compatible base URL. It requests JSON-schema-shaped outputs and validates them with Pydantic. Invalid output enters one bounded repair attempt before returning a visible structured error.

No exact DeepSeek model identifier is hardcoded as a requirement. The example value in `.env.example` must be changed to the identifier shown in the user's DeepSeek console.

## Error Handling

- File type, size, and parse failures are returned as user-facing errors without stack traces.
- Model timeouts and malformed outputs are retried with exponential backoff within a fixed budget.
- Tool errors are recorded in the trace and never hidden as successful answers.
- Empty retrieval returns an explicit insufficient-evidence response.
- Evaluation continues after individual case failures and records the failure reason.

## Testing and Verification

- `pytest` unit and API tests run entirely in mock mode.
- Each project has a smoke command that starts the API and executes one fixture task.
- Evaluation scripts create machine-readable JSON and portfolio-ready Markdown.
- Secret scanning verifies that no value matching common API-key patterns is committed.
- Dockerfiles are syntax-reviewed but container execution is not claimed on this machine because Docker is unavailable.

## Deliverables

Each project contains:

- Source code and dependency lock or pinned requirements.
- Streamlit UI and FastAPI API.
- Fixture data and twenty-case evaluation set.
- Tests, smoke script, and evaluation command.
- `.env.example`, `.gitignore`, and Dockerfile.
- Chinese and English README content in one README.
- Architecture and data-flow diagrams using Mermaid.
- Resume bullets that reference only measured local results.

## Explicit Non-Goals

- Model training, fine-tuning, or reinforcement learning.
- Production authentication, billing, multi-tenancy, or cloud deployment.
- Arbitrary browser automation.
- Arbitrary Python execution.
- Claims of production scale or independent unaided authorship.
