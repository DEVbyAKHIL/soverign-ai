"""Processor settings from environment variables (no secrets are logged)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessorConfig:
    kafka_bootstrap: str
    dsn: str
    checkpoint_dir: str
    master: str
    trigger: str
    watermark: str
    max_offsets_per_trigger: int
    starting_offsets: str
    shuffle_partitions: int
    ops_port: int

    @classmethod
    def from_env(cls) -> "ProcessorConfig":
        env = os.environ
        return cls(
            kafka_bootstrap=env.get("KAFKA_BOOTSTRAP", "kafka:9092"),
            dsn=env.get("PROCESSOR_DSN", ""),
            checkpoint_dir=env.get("CHECKPOINT_DIR", "/checkpoints"),
            master=env.get("SPARK_MASTER", "local[4]"),
            trigger=env.get("STREAM_TRIGGER", "5 seconds"),
            watermark=env.get("STREAM_WATERMARK", "5 minutes"),
            # 0 = no per-batch cap (see docs/spark-streaming.md: offset-based caps skew event time).
            max_offsets_per_trigger=int(env.get("STREAM_MAX_OFFSETS_PER_TRIGGER", "0")),
            starting_offsets=env.get("STREAM_STARTING_OFFSETS", "earliest"),
            shuffle_partitions=int(env.get("STREAM_SHUFFLE_PARTITIONS", "4")),
            ops_port=int(env.get("PROCESSOR_OPS_PORT", "9100")),
        )
