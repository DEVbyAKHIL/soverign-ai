"""Application configuration loaded from environment variables only."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    app_version: str = "0.1.0"

    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "sovereign-ai-gateway"
    jwt_audience: str = "sovereign-ai-gateway-api"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)

    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    max_request_bytes: int = 1_048_576
    audit_retention_days: int = 365
    policy_dir: str = str(Path(__file__).resolve().parents[1] / "policy" / "policies")
    rate_limit_per_minute: int = 60

    # Master key for keyed entity hashes and pseudonyms. Separate from JWT_SECRET; rotating it
    # changes all future hashes/pseudonyms (old audit records remain verifiable).
    entity_hash_key: str = Field(min_length=32)
    # required: refuse to start without NER. optional: run regex+classifier only (dev hosts).
    ner_mode: Literal["required", "optional", "disabled"] = "required"
    # Optional local LLM reviewer for uncertain injection verdicts (OpenAI-compatible endpoint you
    # host, e.g. http://ollama:11434/v1). Empty = disabled. Never point this at a third-party API:
    # it receives the untrusted content under review.
    llm_reviewer_url: str = ""
    llm_reviewer_model: str = "llama3.1:8b"
    llm_reviewer_timeout_s: float = Field(default=3.0, gt=0, le=30)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard(cls, v: list[str]) -> list[str]:
        if "*" in v:
            raise ValueError("Wildcard CORS origin is not permitted")
        return v

    @field_validator("jwt_secret", "entity_hash_key")
    @classmethod
    def _reject_placeholder(cls, v: str) -> str:
        if v.startswith("CHANGE_ME"):
            raise ValueError("secret still contains the placeholder value")
        return v

    @property
    def is_development(self) -> bool:
        return self.environment in ("development", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
