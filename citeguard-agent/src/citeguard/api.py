from __future__ import annotations

from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel

from .config import Settings
from .ingestion import chunk_document, parse_bytes
from .models import AgentResult
from .retrieval import BM25Index
from .workflow import build_fixture_agent, run_agent

app = FastAPI(title="CiteGuard Agent")
_index = build_fixture_agent().index


class AskRequest(BaseModel):
    question: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mock": not Settings.from_env().model_enabled}


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)) -> dict:
    document = parse_bytes(file.filename or "upload.txt", await file.read())
    global _index
    _index = BM25Index(chunk_document(document))
    return {"document_id": document.id, "chunks": len(_index.chunks)}


@app.post("/ask", response_model=AgentResult)
def ask(request: AskRequest) -> AgentResult:
    return run_agent(request.question, _index)
