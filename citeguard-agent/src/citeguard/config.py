"""Environment-backed configuration for CiteGuard."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    mock: bool = False
    api_key: str | None = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    max_retries: int = 3
    max_file_mb: int = 10

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment without logging or printing secrets."""
        api_key = os.getenv("DEEPSEEK_API_KEY")
        api_key = api_key.strip() if api_key and api_key.strip() else None
        base_url = os.getenv("DEEPSEEK_BASE_URL", cls.base_url).strip() or cls.base_url
        model = os.getenv("DEEPSEEK_MODEL", cls.model).strip() or cls.model
        return cls(
            mock=_env_bool("CITEGUARD_MOCK"),
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_retries=_env_int("MAX_RETRIES", cls.max_retries),
            max_file_mb=_env_int("MAX_FILE_MB", cls.max_file_mb),
        )

    @property
    def model_enabled(self) -> bool:
        """Whether requests to the configured model are permitted."""
        return not self.mock and bool(self.api_key)
