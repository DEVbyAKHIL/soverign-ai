"""event-producer CLI.

python -m producer continuous                 # rate-limited, runs until SIGTERM
python -m producer backfill --total 1000000   # fixed number of events, then exit
python -m producer verify --run-id <id> --expect N
python -m producer verify-1m [--total N]      # backfill + verify, prints a summary, exit 0/1
"""

import argparse
import asyncio
import sys
import time
import uuid

from events.runtime import Heartbeat, serve_ops, start_with_retry, stop_event
from gateway.logging_config import configure_logging, get_logger
from producer.config import ProducerSettings
from producer.run import ProduceStats, backfill, continuous
from producer.verify import Tally, read_run
from publisher.sender import KafkaSender

log = get_logger("event-producer")


def _sender(cfg: ProducerSettings) -> KafkaSender:
    return KafkaSender(cfg.kafka_bootstrap, timeout_s=60, client_id="event-producer")


def _progress(every: int):
    marks = {"next": every}

    def report(s: ProduceStats) -> None:
        if s.generated >= marks["next"]:
            log.info("backfill_progress", generated=s.generated, acked=s.acked)
            marks["next"] += every

    return report


async def cmd_continuous(cfg: ProducerSettings) -> int:
    stop = stop_event()
    hb = Heartbeat(max_age_s=60)
    ops = await serve_ops(hb, cfg.producer_ops_port)
    sender = _sender(cfg)
    try:
        if not await start_with_retry(sender.start, stop, hb.beat):
            return 0
        log.info("producer_continuous_started", rate=cfg.producer_rate, topic=cfg.producer_topic)
        stats = await continuous(sender, cfg, stop, beat=hb.beat)
        log.info("producer_stopped", generated=stats.generated, acked=stats.acked)
    finally:
        await sender.stop()
        ops.close()
    return 0


async def run_backfill(
    cfg: ProducerSettings, total: int, run_id: str, live: bool = False
) -> ProduceStats:
    stop = stop_event()
    sender = _sender(cfg)
    if not await start_with_retry(sender.start, stop, lambda: None):
        raise SystemExit(1)
    try:
        log.info(
            "backfill_started",
            total=total,
            run_id=run_id,
            seed=cfg.producer_seed,
            live=live,
            topic=cfg.producer_topic,
        )
        return await backfill(
            sender,
            cfg,
            total=total,
            run_id=run_id,
            stop=stop,
            progress=_progress(100_000),
            live=live,
        )
    finally:
        await sender.stop()


def _row(label: str, value: object) -> str:
    text = f"{value:,}" if isinstance(value, int) else str(value)
    return f"{label:<40}{text}"


def summary(run_id: str, stats: ProduceStats | None, t: Tally, expect: int, secs: float) -> bool:
    checks = {f"distinct event_ids >= {expect:,}": t.distinct >= expect}
    checks["every valid event has a UTC timestamp"] = t.ts_valid == t.schema_valid
    if stats is not None:
        checks["no Kafka delivery failures"] = stats.delivery_failed == 0
        checks["every acknowledged message read back"] = t.read == stats.acked
        checks["schema-valid == generated valid + duplicates"] = (
            t.schema_valid == stats.valid + stats.duplicates
        )
        checks["invalid read back == invalid injected"] = (
            sum(t.invalid.values()) == stats.invalid_total
        )
        checks["distinct event_ids == generated valid"] = t.distinct == stats.valid
    lines = ["", "=" * 64, "Phase 1 ingestion verification (producer -> Kafka)", "=" * 64]
    lines.append(_row("Run id:", run_id))
    if stats is not None:
        lines += [
            _row("Events generated:", stats.generated),
            _row("Events produced (acked by Kafka):", stats.acked),
            _row("Injected invalid events:", stats.invalid_total),
            _row("Injected duplicate deliveries:", stats.duplicates),
            _row("Produce time (s):", f"{stats.seconds:.1f}"),
            _row("Produce rate (messages/s):", f"{stats.sent / max(stats.seconds, 1e-9):,.0f}"),
        ]
    lines += [
        _row("Events read back from Kafka:", t.read),
        _row("Valid against schema 1.0.0:", t.schema_valid),
        _row("Valid UTC timestamps:", t.ts_valid),
        _row("Distinct event_ids:", t.distinct),
        _row("Duplicate event_ids (deduplicated):", t.duplicates),
        _row("Invalid (dead-letter candidates):", sum(t.invalid.values())),
        _row("Event-time range:", f"{t.min_ts} .. {t.max_ts}"),
        _row("Verify time (s):", f"{secs:.1f}"),
    ]
    lines += [_row(f"  invalid[{r}]:", n) for r, n in sorted(t.invalid.items())]
    lines.append("-" * 64)
    lines += [f"[{'PASS' if ok else 'FAIL'}] {name}" for name, ok in checks.items()]
    passed = all(checks.values())
    lines.append(f"RESULT: {'PASSED' if passed else 'FAILED'}")
    print("\n".join(lines), flush=True)  # noqa: T201 - CLI report
    return passed


async def cmd_verify(
    cfg: ProducerSettings, run_id: str, expect: int, stats: ProduceStats | None = None
) -> int:
    started = time.perf_counter()
    tally = await read_run(cfg.kafka_bootstrap, cfg.producer_topic, run_id)
    return 0 if summary(run_id, stats, tally, expect, time.perf_counter() - started) else 1


async def cmd_verify_1m(cfg: ProducerSettings, total: int) -> int:
    run_id = uuid.uuid4().hex[:12]
    stats = await run_backfill(cfg, total, run_id)
    return await cmd_verify(cfg, run_id, total - stats.invalid_total, stats)


def main(argv: list[str] | None = None) -> int:
    cfg = ProducerSettings()
    configure_logging(cfg.log_level)
    p = argparse.ArgumentParser(prog="producer")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("continuous")
    b = sub.add_parser("backfill")
    b.add_argument("--total", type=int, default=cfg.producer_total)
    b.add_argument("--run-id", default=None)
    b.add_argument("--seed", type=int, default=None, help="override PRODUCER_SEED (new tenants)")
    b.add_argument("--live", action="store_true", help="event time = production time")
    b.add_argument("--tenant-ids", default=None, help="comma-separated tenant UUIDs to use")
    v = sub.add_parser("verify")
    v.add_argument("--run-id", required=True)
    v.add_argument("--expect", type=int, required=True)
    m = sub.add_parser("verify-1m")
    m.add_argument("--total", type=int, default=1_000_000)
    args = p.parse_args(argv)

    if args.cmd == "continuous":
        return asyncio.run(cmd_continuous(cfg))
    if args.cmd == "backfill":
        run_id = args.run_id or uuid.uuid4().hex[:12]
        if args.seed is not None:
            cfg = cfg.model_copy(update={"producer_seed": args.seed})
        if args.tenant_ids:
            cfg = cfg.model_copy(update={"producer_tenant_ids": args.tenant_ids})
        stats = asyncio.run(run_backfill(cfg, args.total, run_id, live=args.live))
        log.info(
            "backfill_done",
            run_id=run_id,
            valid=stats.valid,
            invalid=stats.invalid_total,
            duplicates=stats.duplicates,
            generated=stats.generated,
            acked=stats.acked,
            delivery_failed=stats.delivery_failed,
            seconds=round(stats.seconds, 1),
        )
        return 0 if stats.delivery_failed == 0 else 1
    if args.cmd == "verify":
        return asyncio.run(cmd_verify(cfg, args.run_id, args.expect))
    return asyncio.run(cmd_verify_1m(cfg, args.total))


if __name__ == "__main__":
    sys.exit(main())
