from fastapi.testclient import TestClient

from datapilot.api import app


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
