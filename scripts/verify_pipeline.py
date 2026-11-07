"""End-to-end verification of the streaming pipeline with a processor restart in the middle.

    python scripts/verify_pipeline.py [--total 1000000] [--restart-at 0.4] [--no-restart]

Runs on the host and drives Docker Compose (the stack must be up, incl. spark-stream-processor):
  1. backfills TOTAL events under a fresh run id and seed (new tenants, so the run's aggregates are
     separable from other traffic), with live event times;
  2. once the processor has consumed RESTART_AT of them, kills spark-stream-processor (SIGKILL,
     no graceful stop) and starts it again; it must resume from its checkpoints;
  3. waits until the run is fully consumed and the aggregates stop changing;
  4. reconciles: produced (Kafka acks) = consumed = valid + dead-letter, and the sum of every
     tumbling-window aggregate = valid events. Any mismatch -> exit code 1.
Numbers are read from Postgres; nothing is estimated.
"""

import argparse
import json
import random
import subprocess  # nosec B404 - drives docker compose with fixed argument lists
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # standard library only: needs Python + Docker

NIL = "00000000-0000-0000-0000-000000000000"


def compose(*args: str, capture: bool = True, check: bool = True) -> str:
    res = subprocess.run(  # noqa: S603  # nosec B603 B607 - fixed argv, no shell
        ["docker", "compose", *args],  # noqa: S607
        cwd=ROOT,
        capture_output=capture,
        text=True,
        check=check,
    )
    return res.stdout if capture else ""


def sql(query: str) -> str:
    """Run a read-only query through psql inside the postgres container (SQL on stdin)."""
    psql = 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAq'
    res = subprocess.run(  # noqa: S603  # nosec B603 B607 - fixed argv
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", psql],  # noqa: S607
        cwd=ROOT,
        input=query,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def num(query: str) -> int:
    v = sql(query)
    return int(float(v)) if v else 0


def run_counts(run_id: str) -> tuple[int, int, int]:
    row = sql(
        "select coalesce(sum(consumed),0), coalesce(sum(valid),0), coalesce(sum(invalid),0) "  # nosec B608 - generated UUIDs and constants only
        f"from pipeline_run_counts where run_id = '{run_id}'"
    )
    a, b, c = (int(x) for x in row.split("|"))
    return a, b, c


def tumbling_sum(table: str, metric: str, tenants: str, extra: str = "") -> int:
    return num(
        f"select coalesce(sum(value),0) from {table} where metric = '{metric}' "  # nosec B608 - generated UUIDs and constants only
        f"and window_type = 'tumbling' and tenant_id in ({tenants}) {extra}"
    )


def tumbling_totals(tenants: str) -> dict[str, int]:
    """Events counted by each tumbling aggregation (each event lands in exactly one window)."""
    pii = (
        "select coalesce(sum(event_count),0) from aggregated_pii_metrics where "  # nosec B608
        f"metric = 'pii_entities' and tenant_id in ({tenants})"
    )
    return {
        "requests per tenant (1m)": tumbling_sum(
            "aggregated_request_metrics", "requests", tenants, f"and agent_id = '{NIL}'"
        ),
        "decisions by type (1m)": tumbling_sum("aggregated_policy_metrics", "count", tenants),
        "PII per model (5m)": num(pii),
        "sovereignty (10m)": tumbling_sum("aggregated_sovereignty_metrics", "events", tenants),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=1_000_000)
    ap.add_argument("--restart-at", type=float, default=0.4)
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--timeout-s", type=int, default=2700)
    ap.add_argument("--drain-timeout-s", type=int, default=900)
    args = ap.parse_args()

    seed = random.SystemRandom().randrange(10**6, 10**7)
    run_id = f"p2-{uuid.uuid4().hex[:10]}"
    tenant_ids = [str(uuid.uuid4()) for _ in range(5)]  # fresh tenants isolate this run
    tenants = ",".join(f"'{t}'" for t in tenant_ids)
    print(f"run_id={run_id} seed={seed} total={args.total:,}", flush=True)

    started = time.monotonic()
    backfill = subprocess.Popen(  # noqa: S603  # nosec B603 B607 - fixed argv
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "-T",
            "event-producer",
            "python",
            "-m",
            "producer",  # noqa: S607
            "backfill",
            "--total",
            str(args.total),
            "--run-id",
            run_id,
            "--seed",
            str(seed),
            "--live",
            "--tenant-ids",
            ",".join(tenant_ids),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    restarted_at = None
    restart_done = args.no_restart
    output: list[str] = []
    while backfill.poll() is None or not restart_done:
        consumed = run_counts(run_id)[0]
        if not restart_done and consumed >= args.restart_at * args.total:
            print(f"killing spark-stream-processor at consumed={consumed:,}", flush=True)
            compose("kill", "spark-stream-processor")
            compose("up", "-d", "--no-deps", "spark-stream-processor")
            restarted_at, restart_done = consumed, True
        if backfill.poll() is None:
            time.sleep(3)
        elif not restart_done:
            time.sleep(3)
        if time.monotonic() - started > args.timeout_s:
            print("timeout while producing", flush=True)
            break
    output = (backfill.stdout.read() if backfill.stdout else "").splitlines()
    produce_s = time.monotonic() - started
    done = next((json.loads(x) for x in output if '"backfill_done"' in x), None)
    if done is None:
        print("\n".join(output[-20:]))
        print("backfill did not report completion", flush=True)
        return 1
    produced, valid_expected = done["acked"], done["valid"]

    # Drain: wait until every tumbling aggregation has counted every valid event. "Unchanged for
    # a while" is NOT a completion signal: after a restart one query can spend minutes in a single
    # catch-up batch. Only when --drain-timeout-s passes without completion is the gap reported.
    drain_started = None
    while time.monotonic() - started < args.timeout_s:
        consumed, valid, invalid = run_counts(run_id)
        if consumed >= produced:
            drain_started = drain_started or time.monotonic()
            totals = tumbling_totals(tenants)
            if all(v >= valid for v in totals.values()):
                break
            if time.monotonic() - drain_started > args.drain_timeout_s:
                print(f"drain timeout: {totals}", flush=True)
                break
        time.sleep(10)
    total_s = time.monotonic() - started

    consumed, valid, invalid = run_counts(run_id)
    agg = tumbling_totals(tenants)
    sliding_rows = num(
        "select count(*) from (select 1 from aggregated_latency_metrics where tenant_id in "  # nosec B608 - generated UUIDs and constants only
        f"({tenants}) union all select 1 from aggregated_agent_risk_metrics where tenant_id in "
        f"({tenants}) union all select 1 from aggregated_injection_metrics where tenant_id in "
        f"({tenants})) s"
    )
    aggregated = agg["requests per tenant (1m)"]
    checks = {
        "consumed == produced": consumed == produced,
        "valid + dead-letter == consumed": valid + invalid == consumed,
        "valid == producer's valid count": valid == valid_expected,
        "aggregated == valid (each event in exactly one 1-min window)": aggregated == valid,
        "every tumbling aggregation sums to the same total": len(set(agg.values())) == 1,
        "sliding-window aggregates produced": sliding_rows > 0,
    }
    if not args.no_restart:
        checks["processor restarted mid-run"] = restarted_at is not None and restarted_at < produced

    print("\n" + "=" * 66)
    print("Streaming pipeline verification (Kafka -> Spark -> PostgreSQL)")
    print("=" * 66)
    print(f"Run id / seed:                {run_id} / {seed}")
    print(f"Events produced:              {produced:,}")
    print(f"Events consumed:              {consumed:,}")
    print(f"Events aggregated:            {aggregated:,}")
    print(f"Dead-letter events:           {invalid:,}")
    print(f"Not aggregated (late/missing): {valid - min(agg.values()):,}")
    for name, value in agg.items():
        print(f"  tumbling sum, {name + ':':<27}{value:,}")
    print(f"  sliding-window rows (latency, injections, agent risk): {sliding_rows:,}")
    if restarted_at is not None:
        print(f"Processor killed/restarted at: {restarted_at:,} events consumed")
    print(f"Produce time: {produce_s:,.0f} s   end-to-end until fully aggregated: {total_s:,.0f} s")
    print("-" * 66)
    for name, ok in checks.items():
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    recovery = "PASSED" if all(checks.values()) else "FAILED"
    if not args.no_restart:
        print(f"Processor restart recovery: {recovery}")
    print(f"RESULT: {recovery}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
