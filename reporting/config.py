"""reporting-api settings. Only what this service needs: no entity-hash key, no Redis, no model."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ReportingSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    app_version: str = "0.1.0"
    reporting_database_url: str = "postgresql+asyncpg://reporting_ro:x@localhost:5432/gateway"

    # Token verification only (same issuer/audience/secret as the gateway that mints tokens).
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "sovereign-ai-gateway"
    jwt_audience: str = "sovereign-ai-gateway-api"

    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    max_request_bytes: int = 16_384  # GET-only API
    default_range_minutes: int = Field(default=60, ge=1, le=10_080)
    max_range_days: int = Field(default=7, ge=1, le=90)
    max_page_size: int = Field(default=1000, ge=1, le=10_000)
    # Cells of PII / sovereignty metrics backed by fewer events are withheld (re-identification).
    suppress_below: int = Field(default=5, ge=1, le=1000)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        return [o.strip() for o in v.split(",") if o.strip()] if isinstance(v, str) else v

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard(cls, v: list[str]) -> list[str]:
        if "*" in v:
            raise ValueError("Wildcard CORS origin is not permitted")
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _no_placeholder(cls, v: str) -> str:
        if v.startswith("CHANGE_ME"):
            raise ValueError("secret still contains the placeholder value")
        return v

    @property
    def is_development(self) -> bool:
        return self.environment in ("development", "test")


@lru_cache
def get_reporting_settings() -> ReportingSettings:
    return ReportingSettings()
