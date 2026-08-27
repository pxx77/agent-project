def test_mock_mode_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("CITEGUARD_MOCK", "1")
    from citeguard.config import Settings

    settings = Settings.from_env()

    assert settings.mock is True
    assert settings.model_enabled is False
