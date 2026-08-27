from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel

from .config import Settings
from .ingestion import load_bytes, load_fixture
from .workflow import run_agent

app = FastAPI(title="DataPilot Agent")
_handle = load_fixture()


class AskRequest(BaseModel):
    question: str


@app.get("/health")
def health():
    return {"status": "ok", "mock": not Settings.from_env().model_enabled}


@app.post("/profile")
async def profile(file: UploadFile = File(...)):
    from .profiling import profile_dataset
    return profile_dataset(load_bytes(file.filename or "data.csv", await file.read())).model_dump()


@app.post("/ask")
def ask(request: AskRequest):
    return run_agent(request.question, _handle).model_dump()
