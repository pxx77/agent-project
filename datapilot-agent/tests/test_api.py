from fastapi.testclient import TestClient

from datapilot.api import app
from datapilot.config import Settings


def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_explains_active_model(monkeypatch):
    monkeypatch.setenv("DATAPILOT_MOCK", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test-model")

    response = TestClient(app).get("/health")

    assert response.json()["provider"] == "deepseek"
    assert response.json()["model"] == "deepseek-test-model"
    assert response.json()["mock"] is False


def test_health_reports_the_price_quote_behind_the_cost_figures():
    """费用口径要能从接口查到，否则报告里的「元」无从核对。"""
    pricing = TestClient(app).get("/health").json()["pricing"]

    assert pricing["model"] == Settings.from_env().model
    assert pricing["currency"] == "CNY"
    assert pricing["unit"] == "per_million_tokens"
    assert pricing["verified_on"]
    assert pricing["source"].startswith("https://")
    assert pricing["cache_miss_input"] is not None
    assert pricing["output"] is not None


def test_health_leaves_prices_empty_for_an_unpriced_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "some-other-vendor-model")

    pricing = TestClient(app).get("/health").json()["pricing"]

    assert pricing["model"] == "some-other-vendor-model"
    # 没有单价就留空，不拿别的模型的价目表顶上。
    assert pricing["cache_hit_input"] is None
    assert pricing["cache_miss_input"] is None
    assert pricing["output"] is None


def test_ask_returns_token_usage_and_cost(monkeypatch):
    monkeypatch.setenv("DATAPILOT_MOCK", "1")

    response = TestClient(app).post("/ask", json={"question": "What is revenue by region?"})

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace["usage"]["calls"] == trace["model_calls"] == 1
    # 离线回放是估算，接口必须把这件事标出来。
    assert trace["usage"]["measured"] is False
    assert trace["billing_model"]
    assert trace["cost_cny"] > 0


def test_uploaded_dataset_is_used_by_ask(monkeypatch):
    monkeypatch.setenv("DATAPILOT_MOCK", "1")
    client = TestClient(app)
    upload = client.post(
        "/profile",
        files={"file": ("campaign.csv", b"segment,amount\nA,10\nB,20\n", "text/csv")},
    )
    assert upload.status_code == 200

    answer = client.post("/ask", json={"question": "Show amount by segment"})

    assert answer.status_code == 200
    assert "campaign" in answer.json()["sql"].lower()
    assert answer.json()["query_result"]["error"] is None


def test_swagger_explains_upload_then_ask():
    client = TestClient(app)
    guide = client.get("/")
    schema = client.get("/openapi.json").json()

    assert guide.status_code == 200
    assert guide.json()["steps"][0]["endpoint"] == "POST /profile"
    assert schema["paths"]["/ask"]["post"]["summary"] == "提交问题并运行 Agent"
    assert schema["paths"]["/ask"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("AgentResult")
    assert schema["paths"]["/profile"]["post"]["tags"] == ["1. 上传数据"]
    assert schema["components"]["schemas"]["AskRequest"]["example"]["question"]
