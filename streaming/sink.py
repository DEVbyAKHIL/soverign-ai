"""PostgreSQL sink: idempotent upserts of aggregate rows and pipeline progress.

Spark's `update` output mode emits the *current* value of every window that changed in a
micro-batch. Writing it with INSERT ... ON CONFLICT DO UPDATE on the (window, dimensions, metric)
primary key makes the sink idempotent: a batch replayed after a crash writes the same values again.
Table and column names come only from the constants below (never from data).
"""

import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc  # noqa: UP017 - Python 3.10 in the Spark image

# table -> (dimension column, SQL cast) after tenant_id
TABLE_DIMS: dict[str, tuple[tuple[str, str], ...]] = {
    "aggregated_request_metrics": (("agent_id", "uuid"),),
    "aggregated_policy_metrics": (("decision", "text"),),
    "aggregated_latency_metrics": (),
    "aggregated_pii_metrics": (("destination_model", "text"),),
    "aggregated_injection_metrics": (("agent_id", "uuid"),),
    "aggregated_agent_risk_metrics": (("agent_id", "uuid"),),
    "aggregated_sovereignty_metrics": (
        ("destination_jurisdiction", "text"),
        ("sensitivity_level", "text"),
    ),
}


def upsert_sql(table: str) -> str:
    dims = TABLE_DIMS[table]  # KeyError for anything that is not a known table
    cols = ["window_start", "window_end", "window_type", "tenant_id", *(d for d, _ in dims)]
    cols += ["metric", "value", "event_count", "query_name", "batch_id"]
    placeholders = ["%s", "%s", "%s", "%s::uuid", *(f"%s::{c}" for _, c in dims)]
    placeholders += ["%s", "%s", "%s", "%s", "%s"]
    return (
        f"INSERT INTO {table} ({', '.join(cols)}, processed_at) "  # noqa: S608  # nosec B608
        f"VALUES ({', '.join(placeholders)}, now()) "
        f"ON CONFLICT ON CONSTRAINT pk_{table} DO UPDATE SET "
        "value = EXCLUDED.value, event_count = EXCLUDED.event_count, "
        "query_name = EXCLUDED.query_name, batch_id = EXCLUDED.batch_id, processed_at = now()"
    )


PROGRESS_SQL = (
    "INSERT INTO pipeline_progress "
    "(query_name, batch_id, input_rows, valid_rows, invalid_rows, output_rows, processed_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, now()) "
    "ON CONFLICT ON CONSTRAINT pk_pipeline_progress DO UPDATE SET "
    "input_rows = EXCLUDED.input_rows, valid_rows = EXCLUDED.valid_rows, "
    "invalid_rows = EXCLUDED.invalid_rows, output_rows = EXCLUDED.output_rows, processed_at = now()"
)
RUN_COUNTS_SQL = (
    "INSERT INTO pipeline_run_counts (run_id, batch_id, consumed, valid, invalid, processed_at) "
    "VALUES (%s, %s, %s, %s, %s, now()) "
    "ON CONFLICT ON CONSTRAINT pk_pipeline_run_counts DO UPDATE SET "
    "consumed = EXCLUDED.consumed, valid = EXCLUDED.valid, invalid = EXCLUDED.invalid, "
    "processed_at = now()"
)


def utc(ts: datetime) -> datetime:
    """Spark hands back naive datetimes in the session time zone (UTC)."""
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


class PostgresSink:
    def __init__(self, dsn: str, retries: int = 4, backoff_s: float = 0.5) -> None:
        self.dsn, self.retries, self.backoff_s = dsn, retries, backoff_s

    def _run(self, work) -> None:
        """One transaction per call; transient failures retried with exponential backoff. The
        last failure propagates, failing the micro-batch, so Spark replays it after restart."""
        import psycopg  # imported lazily: host tests of the SQL do not need the driver

        for attempt in range(1, self.retries + 1):
            try:
                with psycopg.connect(self.dsn, connect_timeout=10) as conn, conn.cursor() as cur:
                    work(cur)
                return
            except psycopg.OperationalError:
                if attempt == self.retries:
                    raise
                time.sleep(self.backoff_s * 2 ** (attempt - 1))

    def write(
        self,
        rows_by_table: dict[str, list[Sequence[Any]]],
        progress: Sequence[Any] | None = None,
        run_counts: Iterable[Sequence[Any]] = (),
    ) -> None:
        def work(cur) -> None:
            for table, rows in rows_by_table.items():
                if rows:
                    cur.executemany(upsert_sql(table), rows)
            run_rows = list(run_counts)
            if run_rows:
                cur.executemany(RUN_COUNTS_SQL, run_rows)
            if progress is not None:
                cur.execute(PROGRESS_SQL, progress)

        self._run(work)
