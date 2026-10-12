from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Read from environment / .env, never from code."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    database_url: str = "sqlite:///./resume_screener.db"

    # Bearer key for the API. If unset, every /api route except /api/health answers 503.
    api_key_admin: SecretStr | None = None

    # Interactive API docs (/docs, /redoc, /openapi.json) are off unless explicitly enabled.
    enable_docs: bool = False

    max_upload_mb: int = Field(default=10, gt=0)
    max_files_per_upload: int = Field(default=10, gt=0)
    upload_rate_limit_per_minute: int = Field(default=20, gt=0)
    # Candidates older than this are hard-deleted. 0 disables retention. Demo rows are exempt.
    retention_days: int = Field(default=90, ge=0)

    # Default scoring weights on a 0-100 scale (relative; renormalised over applicable components).
    # They can be overridden at runtime from the Settings page (stored in the DB).
    weight_must_have: float = Field(default=40, ge=0, le=100)
    weight_nice_to_have: float = Field(default=10, ge=0, le=100)
    weight_experience: float = Field(default=15, ge=0, le=100)
    weight_semantic: float = Field(default=20, ge=0, le=100)
    weight_language_education: float = Field(default=15, ge=0, le=100)

    # Cosine similarity is mapped linearly from [floor, ceil] to [0, 1].
    semantic_floor: float = 0.2
    semantic_ceil: float = 0.8

    @field_validator("openai_api_key", "api_key_admin", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
