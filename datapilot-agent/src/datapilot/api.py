from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from . import state
from .config import Settings
from .ingestion import load_bytes
from .models import AgentResult
from .usage import PRICE_CURRENCY, PRICE_SOURCE, PRICE_UNIT, PRICE_VERIFIED_ON, price_for
from .workflow import run_agent

app = FastAPI(
    title="DataPilot 数据分析 Agent",
    version="1.0.0",
    description="""
## 使用顺序

1. 打开 **1. 上传数据**，调用 `POST /profile` 上传 CSV、XLSX 或 SQLite。
2. 打开 **2. 提问**，调用 `POST /ask`，点击 **Try it out** 后填写自然语言问题。
3. 查看生成的 `sql`、`query_result`、`chart`、`verification` 和 `trace`。
4. `trace.usage` 给出本次消耗的 token，`trace.cost_cny` 是按 `trace.billing_model` 官方单价折算的费用。
5. `trace.provider=deepseek` 才表示 SQL 由 DeepSeek 生成；`mock` 表示离线演示模式。
""",
    openapi_tags=[
        {"name": "0. 状态", "description": "确认服务和当前模型模式。"},
        {"name": "1. 上传数据", "description": "上传数据并设为后续提问的数据源。"},
        {"name": "2. 提问", "description": "针对最近上传的数据生成并执行安全只读 SQL。"},
    ],
)


class AskRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"question": "按地区统计销售额，并按销售额降序排列。"}}
    )

    question: str = Field(
        min_length=1,
        description="针对当前数据集提出的自然语言分析问题。",
        examples=["按地区统计销售额，并按销售额降序排列。"],
    )


@app.get("/", include_in_schema=False)
def usage_guide() -> dict:
    return {
        "name": "DataPilot 数据分析 Agent",
        "swagger": "/docs",
        "steps": [
            {"step": 1, "endpoint": "POST /profile", "action": "上传数据并查看字段画像"},
            {"step": 2, "endpoint": "POST /ask", "action": "在 question 中填写分析问题"},
            {"step": 3, "endpoint": "GET /health", "action": "确认 provider 是否为 deepseek"},
        ],
    }


@app.get(
    "/health",
    tags=["0. 状态"],
    summary="查看当前模型模式与计价口径",
    description=(
        "`provider=deepseek` 且 `mock=false` 才表示提问时会调用 DeepSeek API。"
        "`pricing` 给出本次 `trace.cost_cny` 所使用的单价口径与出处。"
    ),
)
def health():
    settings = Settings.from_env()
    price = price_for(settings.model)
    return {
        "status": "ok",
        "provider": "deepseek" if settings.model_enabled else "mock",
        "model": settings.model if settings.model_enabled else "deterministic-fixture",
        "mock": not settings.model_enabled,
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
    "/profile",
    tags=["1. 上传数据"],
    summary="上传数据并生成字段画像",
    description="选择一个 CSV、XLSX 或 SQLite 文件。上传成功后，该文件会成为 `/ask` 的数据源。",
)
async def profile(file: UploadFile = File(...)):
    from .profiling import profile_dataset

    try:
        handle = load_bytes(file.filename or "data.csv", await file.read())
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state.set_handle(handle)
    return profile_dataset(handle).model_dump()


@app.post(
    "/ask",
    response_model=AgentResult,
    tags=["2. 提问"],
    summary="提交问题并运行 Agent",
    description="点击 **Try it out**，修改请求体中的 `question`，然后点击 **Execute**。",
    response_description="包含安全 SQL、查询结果、图表规格、验证结果、模型调用轨迹，以及 token 用量与费用。",
)
def ask(request: AskRequest):
    return run_agent(request.question, state.get_handle()).model_dump()
