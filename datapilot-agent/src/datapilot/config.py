from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value >= 0 else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    mock: bool = True
    api_key: str | None = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    max_retries: int = 2
    max_rows: int = 1000

    @classmethod
    def from_env(cls) -> "Settings":
        raw = os.getenv("DATAPILOT_MOCK", "1").lower()
        key = os.getenv("DEEPSEEK_API_KEY", "").strip() or None
        return cls(raw in {"1", "true", "yes", "on"}, key, os.getenv("DEEPSEEK_BASE_URL", cls.base_url), os.getenv("DEEPSEEK_MODEL", cls.model), _int("MAX_RETRIES", 2), _int("MAX_ROWS", 1000))

    @property
    def model_enabled(self) -> bool:
        return bool(self.api_key) and not self.mock
