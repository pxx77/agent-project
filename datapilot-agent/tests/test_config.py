def test_datapilot_defaults_to_mock_without_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DATAPILOT_MOCK", "1")
    from datapilot.config import Settings
    settings = Settings.from_env()
    assert settings.mock is True
    assert settings.model_enabled is False


def test_real_mode_builds_deepseek_client(monkeypatch):
    monkeypatch.setenv("DATAPILOT_MOCK", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-test-model")

    from datapilot.config import Settings
    from datapilot.llm import DeepSeekClient, build_model_client

    client = build_model_client(Settings.from_env())

    assert isinstance(client, DeepSeekClient)
    assert client.model == "deepseek-test-model"


def test_api_key_enables_deepseek_when_mock_flag_is_unset(monkeypatch):
    monkeypatch.delenv("DATAPILOT_MOCK", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    from datapilot.config import Settings

    settings = Settings.from_env()

    assert settings.mock is False
    assert settings.model_enabled is True
