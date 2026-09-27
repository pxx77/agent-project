def test_mock_mode_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("CITEGUARD_MOCK", "1")
    from citeguard.config import Settings

    settings = Settings.from_env()

    assert settings.mock is True
    assert settings.model_enabled is False


def test_real_mode_builds_deepseek_client(monkeypatch):
    monkeypatch.setenv("CITEGUARD_MOCK", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test-model")

    from citeguard.config import Settings
    from citeguard.llm import DeepSeekClient, build_model_client

    client = build_model_client(Settings.from_env())

    assert isinstance(client, DeepSeekClient)
    assert client.model == "deepseek-test-model"
