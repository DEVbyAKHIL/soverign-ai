from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PublisherSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"
    kafka_bootstrap: str = "kafka:9092"
    log_level: str = "INFO"
    publisher_batch_size: int = Field(default=500, ge=1, le=10_000)
    publisher_poll_interval_s: float = Field(default=0.5, gt=0, le=60)
    # Per-row retry delay = min(base * 2**(attempts-1), max), minus up to 20% jitter.
    publisher_backoff_base_s: float = Field(default=1.0, gt=0, le=60)
    publisher_backoff_max_s: float = Field(default=300.0, gt=0, le=3600)
    publisher_send_timeout_s: float = Field(default=30.0, gt=0, le=300)
    publisher_ops_port: int = 9100

    @field_validator("database_url")
    @classmethod
    def _valid_dsn(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must be a postgresql+asyncpg:// URL")
        if "CHANGE_ME" in v:
            raise ValueError("DATABASE_URL still contains a placeholder")
        return v
