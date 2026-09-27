from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from . import state
from .config import Settings
from .ingestion import chunk_document, parse_bytes
from .models import AgentResult
from .retrieval import build_index
from .usage import PRICE_CURRENCY, PRICE_SOURCE, PRICE_UNIT, PRICE_VERIFIED_ON, price_for
from .workflow import run_agent

app = FastAPI(
    title="CiteGuard 文档研究 Agent",
    version="1.0.0",
    description="""
## 使用顺序

1. 打开 **1. 上传文档**，调用 `POST /ingest` 上传 TXT、Markdown、PDF 或 DOCX。
2. 打开 **2. 提问**，调用 `POST /ask`，点击 **Try it out** 后在 `question` 中填写问题。
3. 查看返回值中的 `answer`、`evidence`、`verification` 和 `trace`。
4. `trace.provider=deepseek` 才表示该次回答调用了 DeepSeek；`mock` 表示离线演示模式。
5. `trace.usage` 给出本次调用的 token 数，`trace.cost_cny` 是按 `GET /health` 中 `pricing` 单价折算的费用。
""",
    openapi_tags=[
        {"name": "0. 状态", "description": "确认服务和当前模型模式。"},
        {"name": "1. 上传文档", "description": "上传文档并建立本次服务进程使用的索引。"},
        {"name": "2. 提问", "description": "针对最近上传的文档运行检索、回答和验证。"},
    ],
)


class AskRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"question": "文档对 Agent 评测提出了哪些要求？"}}
    )

    question: str = Field(
        min_length=1,
        description="要针对当前文档索引提出的问题。",
        examples=["文档对 Agent 评测提出了哪些要求？"],
    )


@app.get("/", include_in_schema=False)
def usage_guide() -> dict:
    return {
        "name": "CiteGuard 文档研究 Agent",
        "swagger": "/docs",
        "steps": [
            {"step": 1, "endpoint": "POST /ingest", "action": "上传文档并建立索引"},
            {"step": 2, "endpoint": "POST /ask", "action": "在 question 中填写问题"},
            {"step": 3, "endpoint": "GET /health", "action": "确认 provider 是否为 deepseek"},
        ],
    }


@app.get(
    "/health",
    tags=["0. 状态"],
    summary="查看当前模型模式与计价口径",
    description=(
        "`provider=deepseek` 且 `mock=false` 才表示提问时会调用 DeepSeek API。"
        "`pricing` 字段给出当前模型每次提问的计价口径（元 / 百万 tokens），"
        "`trace.cost_cny` 就是按这套单价算出来的。"
    ),
)
def health() -> dict:
    settings = Settings.from_env()
    index = state.get_index()
    price = price_for(settings.model)
    return {
        "status": "ok",
        "provider": "deepseek" if settings.model_enabled else "mock",
        "model": settings.model if settings.model_enabled else "deterministic-fixture",
        "mock": not settings.model_enabled,
        "retriever": index.name,
        "embedding": settings.embedding_provider,
        "pricing": {
            "model": settings.model,
            "currency": PRICE_CURRENCY,
            "unit": PRICE_UNIT,
            "verified_on": PRICE_VERIFIED_ON,
            "source": PRICE_SOURCE,
            "cache_hit_input": price.cache_hit_input if price else None,
            "cache_miss_input": price.cache_miss_input if price else None,
            "output": price.output if price else None,
        },
    }


@app.post(
    "/ingest",
    tags=["1. 上传文档"],
    summary="上传文档并建立索引",
    description="选择一个 TXT、Markdown、PDF 或 DOCX 文件。上传成功后再调用 `/ask`。",
)
async def ingest(file: UploadFile = File(...)) -> dict:
    settings = Settings.from_env()
    try:
        document = parse_bytes(file.filename or "upload.txt", await file.read())
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    index = build_index(chunk_document(document), settings)
    state.set_index(index)
    return {"document_id": document.id, "chunks": len(index.chunks), "retriever": index.name}


@app.post(
    "/ask",
    response_model=AgentResult,
    tags=["2. 提问"],
    summary="提交问题并运行 Agent",
    description="点击 **Try it out**，修改请求体中的 `question`，然后点击 **Execute**。",
    response_description="包含回答、引用证据、验证结果、模型调用轨迹，以及 token 用量与费用。",
)
def ask(request: AskRequest) -> AgentResult:
    return run_agent(request.question, state.get_index())
