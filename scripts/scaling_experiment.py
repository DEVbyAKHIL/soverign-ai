"""Local horizontal-scaling experiment: one producer vs two concurrent producers.

    python scripts/scaling_experiment.py [--events 300000] [--out docs/evidence/scaling.json]

Standard library only; drives Docker Compose (the stack must be up). For each phase it starts N
backfill producers (fresh tenants, live event time) and samples every few seconds:
  produced     Kafka end offsets of governance.events.raw (all partitions) - includes the small
               continuous-producer and gateway traffic, which is reported separately
  consumed     events the Spark validate query processed for the phase's runs
  aggregated   events counted in the 1-minute tumbling aggregate for the phase's tenants
  lag          produced (since phase start) - consumed
  pg writes    inserts + updates on aggregated_* tables (pg_stat_user_tables)
  cpu / memory docker stats, per container (peak)
This measures *local* horizontal scaling on one Docker host. It is not evidence of production
cluster scalability (single Kafka broker, Spark local mode, shared CPU).
"""

import argparse
import json
import subprocess  # nosec B404 - drives docker compose with fixed argument lists
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sh(args: list[str], stdin: str | None = None) -> str:
    return subprocess.run(  # noqa: S603  # nosec B603 B607 - fixed argv
        args, cwd=ROOT, input=stdin, capture_output=True, text=True, check=True
    ).stdout


def sql(q: str) -> str:
    psql = 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAq'
    return sh(["docker", "compose", "exec", "-T", "postgres", "sh", "-c", psql], q).strip()


def raw_offsets() -> int:
    out = sh(["docker", "compose", "exec", "-T", "kafka", "/opt/kafka/bin/kafka-get-offsets.sh",
              "--bootstrap-server", "kafka:9092", "--topic", "governance.events.raw"])  # fmt: skip
    return sum(int(line.rsplit(":", 1)[1]) for line in out.split() if line.count(":") == 2)


def pg_writes() -> int:
    v = sql(
        "select coalesce(sum(n_tup_ins + n_tup_upd), 0) from pg_stat_user_tables "
        "where relname like 'aggregated_%'"
    )
    return int(v or 0)


def counts(run_ids: list[str], tenants: list[str]) -> tuple[int, int]:
    runs = ",".join(f"'{r}'" for r in run_ids)
    ten = ",".join(f"'{t}'" for t in tenants)
    consumed = sql(
        f"select coalesce(sum(consumed),0) from pipeline_run_counts where run_id in ({runs})"  # nosec B608 - generated run ids only
    )
    aggregated = sql(
        "select coalesce(sum(value),0) from aggregated_request_metrics where metric='requests' "
        f"and window_type='tumbling' and agent_id='00000000-0000-0000-0000-000000000000' "
        f"and tenant_id in ({ten})"  # nosec B608 - generated UUIDs only
    )
    return int(float(consumed)), int(float(aggregated))


def docker_stats() -> dict[str, tuple[float, float]]:
    """container -> (cpu %, memory MiB)."""
    out = sh(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"])
    stats = {}
    for line in out.splitlines():
        name, cpu, mem = line.split("|")
        used = mem.split("/")[0].strip()
        factor = {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024, "B": 1 / 1024 / 1024}
        unit = next(u for u in factor if used.endswith(u))
        stats[name.replace("sovereign-ai-gateway-", "").rsplit("-", 1)[0]] = (
            float(cpu.rstrip("%") or 0),
            float(used[: -len(unit)]) * factor[unit],
        )
    return stats


def phase(producers: int, events: int, timeout_s: int) -> dict:
    runs = [f"scale-{producers}p-{i}-{uuid.uuid4().hex[:6]}" for i in range(producers)]
    tenants = [[str(uuid.uuid4()) for _ in range(5)] for _ in range(producers)]
    all_tenants = [t for ts in tenants for t in ts]
    total = events * producers
    off0, pg0, t0 = raw_offsets(), pg_writes(), time.monotonic()
    procs = [
        subprocess.Popen(  # noqa: S603  # nosec B603 B607 - fixed argv
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "-T",
                "event-producer",
                "python",
                "-m",
                "producer",
                "backfill",
                "--total",
                str(events),
                "--run-id",
                run,
                "--live",
                "--seed",
                str(1000 + i),
                "--tenant-ids",
                ",".join(tenants[i]),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )  # fmt: skip
        for i, run in enumerate(runs)
    ]
    samples, peak = [], {}
    produced_done_at = half_produced_at = half_aggregated_at = None
    while time.monotonic() - t0 < timeout_s:
        now = time.monotonic() - t0
        produced = raw_offsets() - off0
        consumed, aggregated = counts(runs, all_tenants)
        writes = pg_writes() - pg0
        for name, (cpu, mem) in docker_stats().items():
            old = peak.get(name, (0.0, 0.0))
            peak[name] = (max(old[0], cpu), max(old[1], mem))
        samples.append({"t": round(now, 1), "produced": produced, "consumed": consumed,
                        "aggregated": aggregated, "lag": max(0, produced - consumed),
                        "pg_writes": writes})  # fmt: skip
        if half_produced_at is None and produced >= total / 2:
            half_produced_at = now
        if half_aggregated_at is None and aggregated >= total / 2:
            half_aggregated_at = now
        if produced_done_at is None and all(p.poll() is not None for p in procs):
            produced_done_at = now
        if produced_done_at is not None and aggregated >= total:
            break
        time.sleep(3)
    done = [json.loads(x) for p in procs for x in (p.stdout.read() if p.stdout else "").splitlines()
            if '"backfill_done"' in x]  # fmt: skip
    end = samples[-1]
    prod_s = max(d["seconds"] for d in done) if done else None

    def rate(key: str) -> float:
        """Peak rate between consecutive samples."""
        best = 0.0
        for a, b in zip(samples, samples[1:], strict=False):
            if b["t"] > a["t"]:
                best = max(best, (b[key] - a[key]) / (b["t"] - a["t"]))
        return round(best)

    return {
        "producers": producers,
        "events": total,
        "acked": sum(d["acked"] for d in done),
        "produce_seconds": prod_s,
        "produce_rate_eps": round(sum(d["acked"] for d in done) / prod_s) if prod_s else None,
        "consumed": end["consumed"],
        "aggregated": end["aggregated"],
        "peak_spark_rate_eps": rate("consumed"),
        "avg_spark_rate_eps": round(end["consumed"] / end["t"]) if end["t"] else None,
        "peak_kafka_lag": max(s["lag"] for s in samples),
        "peak_pg_write_rate_rows_s": rate("pg_writes"),
        "pg_rows_written": end["pg_writes"],
        "median_latency_s": (
            round(half_aggregated_at - half_produced_at, 1)
            if half_aggregated_at and half_produced_at
            else None
        ),  # fmt: skip
        "completion_latency_s": round(end["t"] - produced_done_at, 1) if produced_done_at else None,
        "wall_seconds": end["t"],
        "peak_cpu_pct": {k: v[0] for k, v in sorted(peak.items())},
        "peak_mem_mib": {k: round(v[1]) for k, v in sorted(peak.items())},
        "complete": end["aggregated"] >= total,
        "samples": samples,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=300_000, help="events per producer")
    ap.add_argument("--timeout-s", type=int, default=1800)
    ap.add_argument("--out", default="docs/evidence/scaling.json")
    a = ap.parse_args()
    results = []
    for n in (1, 2):
        print(f"phase: {n} producer(s) x {a.events:,} events", flush=True)
        results.append(phase(n, a.events, a.timeout_s))
        r = results[-1]
        print(
            f"  produce {r['produce_rate_eps']} ev/s | spark peak {r['peak_spark_rate_eps']} ev/s "
            f"| lag peak {r['peak_kafka_lag']:,} | pg peak {r['peak_pg_write_rate_rows_s']} rows/s "
            f"| median latency {r['median_latency_s']} s | complete {r['complete']}",
            flush=True,
        )
        time.sleep(30)  # let the processor drain before the next phase
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"results: {out}")
    return 0 if all(r["complete"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
