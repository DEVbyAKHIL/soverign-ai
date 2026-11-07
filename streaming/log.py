"""Minimal structured (JSON-lines) logging for the processor: no third-party dependency."""

import json
import sys
from datetime import datetime, timezone


def log(event: str, level: str = "info", **fields) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - Python 3.10
        "level": level,
        "event": event,
        "service": "spark-stream-processor",
        **fields,
    }
    sys.stdout.write(json.dumps(record, default=str) + "\n")
    sys.stdout.flush()
