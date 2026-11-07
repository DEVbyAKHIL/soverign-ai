# Spark Structured Streaming processor

Service `spark-stream-processor` (`infra/docker/spark.Dockerfile`, code in `streaming/`).

| Component | Version / setting |
|---|---|
| Spark | 3.5.8, local mode (`SPARK_MASTER=local[4]`), Java 17, Python 3.10 |
| Kafka connector jars | Downloaded at build time with pinned sha256 checksums (`ADD --checksum`), so nothing is fetched at runtime |
| Driver memory | 2 GB (`PYSPARK_SUBMIT_ARGS`); container limit 3 GB |

## Queries

| Query | Source | Does | Sink |
|---|---|---|---|
| `validate` | `governance.events.raw` | Validates each record against `schemas/governance_event/1.0.0.json`. Uses the **same** Python validator as the producers (`streaming/validation.py`, run as a UDF), so producers and processor share one contract | Valid events → `governance.events.valid` (original key, value and headers). Invalid → `governance.events.dead-letter`. Counts → `pipeline_progress`, `pipeline_run_counts` |
| 8 aggregation queries | `governance.events.valid` | Parse → enrich → watermark (5 min) → dedup `event_id` → window → aggregate (`docs/aggregations.md`) | PostgreSQL `aggregated_*` (upsert) and `pipeline_progress` |

Every query has its own checkpoint directory under `/checkpoints` (Docker volume `sparkcheckpoints`). The processor runs with:
- trigger every 5 s (`STREAM_TRIGGER`);
- no per-batch offset cap by default (`STREAM_MAX_OFFSETS_PER_TRIGGER=0`). Spark splits a cap across partitions by offset count; partitions with different event rates then drift apart in event time, and the watermark drops the slower ones as late. This was measured, not theorised: see `EVALUATION.md`;
- `failOnDataLoss=false`, so retention deleting unread data is logged rather than stopping the job.

### Dead-letter record

```json
{"reason": "decision:enum", "event_id": "…or null", "source_topic": "governance.events.raw",
 "source_partition": 3, "source_offset": 12345, "run_id": "", "failed_at": "2026-11-07T15:00:00.000Z"}
```

The original payload is **not** copied, because an invalid record may contain anything an upstream bug put into it. Operators can read the original from the raw topic by partition and offset while it is retained (7 days). Reasons are value-free codes such as `not_json`, `<field>:<rule>` or `event_timestamp:not_utc`.

## Fault tolerance

| Failure | Behaviour |
|---|---|
| Processor killed (SIGKILL, OOM, crash) | `restart: unless-stopped` brings it back. Each query resumes from its checkpoint: committed Kafka offsets plus the RocksDB state of open windows and of the dedup set. Spark re-runs the last uncommitted micro-batch |
| A micro-batch is re-run | `validate` may write some valid and dead-letter records twice (at-least-once). Aggregation queries drop the duplicates by `event_id`. Upserts make replayed aggregate rows overwrite themselves, and `pipeline_progress` / `pipeline_run_counts` are keyed by batch id, so counts stay exact |
| Postgres unavailable | The sink retries with exponential backoff (4 attempts). If it still fails, the batch fails, the process exits, and after restart the batch is replayed from Kafka. **Already committed aggregates are never deleted or decremented** |
| Malformed event | Goes to the dead letter with a reason; the query keeps running |
| A query stops with an error | The main loop exits non-zero (fail fast), and the restart resumes every query from its checkpoint |
| SIGTERM (`docker compose stop`) | Each query is stopped cleanly (`query.stop()`), then Spark stops. `stop_grace_period: 60s` |

Container details that matter:
- `/tmp` is a `noexec` tmpfs. RocksDB unpacks a native library, so the JVM temp dir and Spark local dirs point at the `sparkwork` volume (`JAVA_TOOL_OPTIONS=-Djava.io.tmpdir=/tmp/spark`).
- The root filesystem is read-only and the process runs as uid 185.

## Database access

The processor connects as `stream_processor`, created by migration 0007 with `PROCESSOR_DB_PASSWORD`. That role can `SELECT/INSERT/UPDATE` only the aggregate and pipeline tables: it has no access to decisions, audit events or the outbox, and no `DELETE` anywhere. A test checks this (`tests/integration/test_aggregate_sink.py`).

## Monitoring

`/healthz` and `/metrics` are served on internal port 9100.

| Endpoint / metric | Meaning |
|---|---|
| `/healthz` | 200 only while every query is active with no error |
| `stream_query_active` | Per query; 1 while it runs |
| `stream_input_rows_per_second`, `stream_processed_rows_per_second` | Per query, from the last batch |
| `stream_batch_input_rows`, `stream_batch_id`, `stream_batch_duration_ms` | Per query, last micro-batch |
| `stream_state_rows` | Rows held in the state store, per query |

Logs are JSON lines; Spark's own logging is set to WARN.
