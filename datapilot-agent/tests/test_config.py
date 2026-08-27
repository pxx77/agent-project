def test_datapilot_defaults_to_mock_without_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DATAPILOT_MOCK", "1")
    from datapilot.config import Settings
    settings = Settings.from_env()
    assert settings.mock is True
    assert settings.model_enabled is False
