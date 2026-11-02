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

    @field_validator("jwt_secret")
    @classmethod
    def _reject_placeholder(cls, v: str) -> str:
        if v.startswith("CHANGE_ME"):
            raise ValueError("JWT_SECRET still contains the placeholder value")
        return v

    @property
    def is_development(self) -> bool:
        return self.environment in ("development", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
