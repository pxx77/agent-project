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


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed >= 0.0 else default


#: Buckets used by the offline hashing encoder; kept here so it stays a plain
#: constant that both the settings and the encoder module can read without a cycle.
DEFAULT_EMBEDDING_DIM = 512

#: Retrieval strategies accepted by ``CITEGUARD_RETRIEVER``.
RETRIEVER_MODES = ("auto", "lexical", "hybrid")


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    mock: bool = False
    api_key: str | None = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    max_retries: int = 3
    max_file_mb: int = 10
    embedding_provider: str = "hashing"
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    retrieval_min_coverage: float = 0.15
    retriever: str = "auto"

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment without logging or printing secrets."""
        api_key = _env_str("DEEPSEEK_API_KEY")
        base_url = _env_str("DEEPSEEK_BASE_URL", cls.base_url) or cls.base_url
        model = _env_str("DEEPSEEK_MODEL", cls.model) or cls.model
        provider = _env_str("CITEGUARD_EMBEDDING_PROVIDER", cls.embedding_provider) or cls.embedding_provider
        retriever = _env_str("CITEGUARD_RETRIEVER", cls.retriever) or cls.retriever
        if retriever not in RETRIEVER_MODES:
            retriever = cls.retriever
        return cls(
            mock=_env_bool("CITEGUARD_MOCK"),
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_retries=_env_int("MAX_RETRIES", cls.max_retries),
            max_file_mb=_env_int("MAX_FILE_MB", cls.max_file_mb),
            embedding_provider=provider,
            embedding_base_url=_env_str("CITEGUARD_EMBEDDING_BASE_URL"),
            embedding_model=_env_str("CITEGUARD_EMBEDDING_MODEL"),
            embedding_api_key=_env_str("CITEGUARD_EMBEDDING_API_KEY"),
            embedding_dim=_env_int("CITEGUARD_EMBEDDING_DIM", cls.embedding_dim)
            or cls.embedding_dim,
            retrieval_min_coverage=_env_float(
                "CITEGUARD_MIN_COVERAGE", cls.retrieval_min_coverage
            ),
            retriever=retriever,
        )

    @property
    def model_enabled(self) -> bool:
        """Whether requests to the configured model are permitted."""
        return not self.mock and bool(self.api_key)
