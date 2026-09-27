from fastapi.testclient import TestClient

from citeguard.api import app


def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_explains_active_model(monkeypatch):
    monkeypatch.setenv("CITEGUARD_MOCK", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test-model")

    response = TestClient(app).get("/health")

    assert response.json()["provider"] == "deepseek"
    assert response.json()["model"] == "deepseek-test-model"
    assert response.json()["mock"] is False


def test_uploaded_document_is_used_by_ask(monkeypatch):
    monkeypatch.setenv("CITEGUARD_MOCK", "1")
    client = TestClient(app)
    upload = client.post(
        "/ingest",
        files={"file": ("policy.md", b"Orchid protocol requires human approval for payments.", "text/markdown")},
    )
    assert upload.status_code == 200

    answer = client.post("/ask", json={"question": "What does the Orchid protocol require?"})

    assert answer.status_code == 200
    assert answer.json()["evidence"][0]["document_id"].startswith("doc-")


def test_swagger_explains_upload_then_ask():
    client = TestClient(app)
    guide = client.get("/")
    schema = client.get("/openapi.json").json()

    assert guide.status_code == 200
    assert guide.json()["steps"][0]["endpoint"] == "POST /ingest"
    assert schema["paths"]["/ask"]["post"]["summary"] == "提交问题并运行 Agent"
    assert schema["paths"]["/ingest"]["post"]["tags"] == ["1. 上传文档"]
    assert schema["components"]["schemas"]["AskRequest"]["example"]["question"]
