from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProducerSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    kafka_bootstrap: str = "kafka:9092"
    log_level: str = "INFO"
    producer_topic: str = "governance.events.raw"
    producer_seed: int = 42
    producer_tenants: int = Field(default=5, ge=1, le=1000)
    producer_agents: int = Field(default=40, ge=1, le=100_000)
    # continuous mode
    producer_rate: float = Field(default=50.0, gt=0, le=100_000)  # events per second
    # backfill mode
    producer_total: int = Field(default=1_000_000, ge=1, le=100_000_000)
    producer_span_hours: float = Field(default=24.0, gt=0, le=24 * 365)
    producer_disorder_s: float = Field(default=30.0, ge=0, le=600)
    producer_chunk: int = Field(default=5_000, ge=1, le=100_000)
    # failure injection (fractions of events)
    producer_failure_rate: float = Field(default=0.0, ge=0, le=1)
    producer_duplicate_rate: float = Field(default=0.0, ge=0, le=1)
    producer_ops_port: int = 9100
