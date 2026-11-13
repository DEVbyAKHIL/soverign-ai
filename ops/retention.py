"""Data retention job: delete data older than its configured retention period.

    python -m ops.retention [--dry-run] [--archive-dir DIR]
    docker compose --profile ops run --rm retention [--dry-run]

Safe:
- Deletes children before parents, so foreign keys never block or cascade unexpectedly
  (detections and audit events before decisions; a decision is only removed once nothing
  references it).
- Audit events are pruned per tenant as a *prefix* of the hash chain. The event_hash of the last
  pruned event is stored in audit_chain_anchors in the same transaction, so verification of the
  remaining chain keeps passing (audit.service.verify_tenant_chain).
- The append-only trigger allows DELETE only when the transaction sets app.audit_retention='on'.
Auditable:
- Every target writes a row to retention_runs (run id, cutoff, rows deleted, dry-run flag).
- Optional JSONL archive of pruned audit events before they are deleted.
Least privilege:
- Runs as the retention_job role (SELECT/DELETE on retained tables only).

Kafka topic retention is configured on the topics themselves (infra/kafka/topics.sh).
"""

import argparse
import asyncio
import gzip
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from gateway.logging_config import configure_logging, get_logger

log = get_logger("retention")

AGGREGATE_TABLES = (
    "aggregated_request_metrics",
    "aggregated_policy_metrics",
    "aggregated_latency_metrics",
    "aggregated_pii_metrics",
    "aggregated_injection_metrics",
    "aggregated_agent_risk_metrics",
    "aggregated_sovereignty_metrics",
)


class RetentionSettings(BaseSettings):
    """Retention periods in days. Validated at start: the job refuses to run on bad values."""

    model_config = SettingsConfigDict(extra="ignore")

    retention_database_url: str = ""
    log_level: str = "INFO"
    retention_decisions_days: int = Field(default=365, ge=1, le=3650)
    retention_detections_days: int = Field(default=365, ge=1, le=3650)
    audit_retention_days: int = Field(default=365, ge=1, le=3650)
    retention_agent_events_days: int = Field(default=30, ge=8, le=3650)  # 7-day baseline + 1
    retention_aggregates_days: int = Field(default=90, ge=1, le=3650)
    retention_pipeline_days: int = Field(default=30, ge=1, le=3650)
    retention_outbox_days: int = Field(default=7, ge=1, le=365)

    @model_validator(mode="after")
    def _check(self) -> "RetentionSettings":
        if not self.retention_database_url:
            raise ValueError("RETENTION_DATABASE_URL is required")
        if "CHANGE_ME" in self.retention_database_url:
            raise ValueError("RETENTION_DATABASE_URL still contains a placeholder")
        return self


@dataclass(frozen=True)
class Result:
    target: str
    cutoff: datetime
    rows: int


async def _record(conn: AsyncConnection, run_id: uuid.UUID, r: Result, dry: bool) -> None:
    await conn.execute(
        text(
            "insert into retention_runs (run_id, target, cutoff, rows_deleted, dry_run) "
            "values (:run, :target, :cutoff, :rows, :dry)"
        ),
        {"run": run_id, "target": r.target, "cutoff": r.cutoff, "rows": r.rows, "dry": dry},
    )


async def _simple(conn, table: str, where: str, cutoff: datetime, dry: bool) -> int:
    """Count or delete rows of a fixed table matching a fixed predicate (no user input in SQL).

    Table names and predicates come only from the constant plan in run(); values are bound."""
    verb = "select count(*) from" if dry else "with d as (delete from"
    if dry:
        return await conn.scalar(text(f"{verb} {table} where {where}"), {"cutoff": cutoff})  # noqa: S608  # nosec B608
    sql = f"{verb} {table} where {where} returning 1) select count(*) from d"  # noqa: S608  # nosec B608
    return await conn.scalar(text(sql), {"cutoff": cutoff})


async def prune_audit(
    conn: AsyncConnection, cutoff: datetime, dry: bool, archive: Path | None
) -> int:
    """Per tenant: drop the chain prefix older than cutoff and move the anchor forward."""
    tenants = (
        await conn.execute(
            text(
                "select tenant_id, max(seq) as last_seq from audit_events "
                "where created_at < :cutoff group by tenant_id"
            ),
            {"cutoff": cutoff},
        )
    ).all()
    total = 0
    for tenant_id, last_seq in tenants:
        rows = (
            await conn.execute(
                text(
                    "select id, seq, decision_id, event_type, actor_id, event_data, "
                    "previous_hash, event_hash, created_at from audit_events "
                    "where tenant_id = :t and seq <= :s order by seq"
                ),
                {"t": tenant_id, "s": last_seq},
            )
        ).all()
        total += len(rows)
        if dry or not rows:
            continue
        if archive is not None:
            archive.mkdir(parents=True, exist_ok=True)
            path = archive / f"audit-{tenant_id}-{rows[-1].seq}.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(
                        json.dumps({**r._asdict(), "tenant_id": tenant_id}, default=str) + "\n"
                    )
        await conn.execute(
            text(
                "insert into audit_chain_anchors "
                "(tenant_id, anchor_seq, anchor_hash, pruned_events, pruned_before, updated_at) "
                "values (:t, :s, :h, :n, :c, now()) on conflict (tenant_id) do update set "
                "anchor_seq = excluded.anchor_seq, anchor_hash = excluded.anchor_hash, "
                "pruned_events = audit_chain_anchors.pruned_events + excluded.pruned_events, "
                "pruned_before = excluded.pruned_before, updated_at = now()"
            ),
            {
                "t": tenant_id,
                "s": rows[-1].seq,
                "h": rows[-1].event_hash,
                "n": len(rows),
                "c": cutoff,
            },
        )
        await conn.execute(text("select set_config('app.audit_retention', 'on', true)"))
        await conn.execute(
            text("delete from audit_events where tenant_id = :t and seq <= :s"),
            {"t": tenant_id, "s": last_seq},
        )
        await conn.execute(text("select set_config('app.audit_retention', 'off', true)"))
    return total


async def run(
    conn_factory, s: RetentionSettings, *, dry: bool, archive: Path | None, now: datetime
) -> list[Result]:
    days = lambda n: now - timedelta(days=n)  # noqa: E731
    run_id = uuid.uuid4()
    plan: list[tuple[str, datetime, object]] = [
        ("event_outbox", days(s.retention_outbox_days),
         ("event_outbox", "published_at is not null and published_at < :cutoff")),
        ("agent_events", days(s.retention_agent_events_days),
         ("agent_events", "created_at < :cutoff")),
        *[(t, days(s.retention_aggregates_days), (t, "window_end < :cutoff"))
          for t in AGGREGATE_TABLES],
        ("pipeline_progress", days(s.retention_pipeline_days),
         ("pipeline_progress", "processed_at < :cutoff")),
        ("pipeline_run_counts", days(s.retention_pipeline_days),
         ("pipeline_run_counts", "processed_at < :cutoff")),
        ("detections", days(s.retention_detections_days),
         ("detections", "created_at < :cutoff")),
        ("audit_events", days(s.audit_retention_days), "audit"),
        # A decision goes only when nothing references it any more (FK-safe).
        ("decisions", days(s.retention_decisions_days),
         ("decisions", "created_at < :cutoff and not exists (select 1 from detections x "
          "where x.decision_id = decisions.id) and not exists (select 1 from audit_events a "
          "where a.decision_id = decisions.id)")),
    ]  # fmt: skip
    results = []
    for target, cutoff, how in plan:
        async with conn_factory() as conn, conn.begin():  # one transaction per target
            if how == "audit":
                rows = await prune_audit(conn, cutoff, dry, archive)
            else:
                table, where = how
                rows = await _simple(conn, table, where, cutoff, dry)
            result = Result(target, cutoff, int(rows or 0))
            await _record(conn, run_id, result, dry)
        results.append(result)
        log.info(
            "retention", target=target, cutoff=cutoff.isoformat(), rows=result.rows, dry_run=dry
        )
    return results


async def main_async(dry: bool, archive: Path | None) -> int:
    s = RetentionSettings()
    configure_logging(s.log_level)
    engine = create_async_engine(s.retention_database_url)
    try:
        results = await run(engine.connect, s, dry=dry, archive=archive, now=datetime.now(UTC))
    finally:
        await engine.dispose()
    width = max(len(r.target) for r in results)
    print(f"{'target':<{width}}  {'cutoff':<25}  {'would delete' if dry else 'deleted'}")  # noqa: T201
    for r in results:
        print(f"{r.target:<{width}}  {r.cutoff.isoformat(timespec='seconds'):<25}  {r.rows:,}")  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ops.retention")
    ap.add_argument("--dry-run", action="store_true", help="count only, delete nothing")
    ap.add_argument(
        "--archive-dir", type=Path, default=None, help="gzip JSONL of pruned audit events"
    )
    args = ap.parse_args(argv)
    return asyncio.run(main_async(args.dry_run, args.archive_dir))


if __name__ == "__main__":
    sys.exit(main())
