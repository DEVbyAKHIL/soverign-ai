# Docker Compose services

All services run locally; nothing is deployed to a cloud provider. Host ports bind to `127.0.0.1` only.

## Default stack (`docker compose up --build -d`)

| Service | Image / build | Role | Host port | Health check | Restart |
|---|---|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | System of record; `event_outbox` | 5432 | `pg_isready` | unless-stopped |
| `redis` | `redis:7.4-alpine` | Rate-limit counters | 6379 | `redis-cli ping` | unless-stopped |
| `migrate` | gateway image | One-shot: `alembic upgrade head` + policy sync | — | exits 0 | no |
| `gateway` | `infra/docker/gateway.Dockerfile` | Security decisions; writes the outbox | 8000 | `/healthz` (image `HEALTHCHECK`) | unless-stopped |
| `ollama` | `ollama/ollama:0.12.6` | Local model backend | 11434 | `ollama list` | unless-stopped |
| `frontend` | `infra/docker/frontend.Dockerfile` | Dashboard (nginx) | 5173 | `wget /` | unless-stopped |
| `kafka` | `apache/kafka:3.9.0` | Event backbone, KRaft | — | `kafka-broker-api-versions.sh` | unless-stopped |
| `kafka-init` | `apache/kafka:3.9.0` | One-shot topic creation (`infra/kafka/topics.sh`) | — | exits 0 | no |
| `event-publisher` | `infra/docker/streaming.Dockerfile` | Outbox → Kafka relay | — (9100 internal) | `/healthz` heartbeat | unless-stopped |
| `event-producer` | `infra/docker/streaming.Dockerfile` | Synthetic telemetry, continuous | — (9100 internal) | `/healthz` heartbeat | unless-stopped |
| `reporting-api` | `infra/docker/reporting.Dockerfile` | Read-only metrics API | 8001 | `/healthz` | unless-stopped |
| `spark-stream-processor` | `infra/docker/spark.Dockerfile` (target `runtime`) | Spark Structured Streaming: validate, aggregate, upsert | — (9100 internal) | `/healthz` = all queries active | unless-stopped |

**Start order** is enforced by `depends_on` conditions:

```text
postgres (healthy) ─► migrate (completed) ─► gateway, event-publisher
kafka (healthy) ─► kafka-init (completed) ─► event-publisher, event-producer, spark-stream-processor
migrate (completed) ─► spark-stream-processor, reporting-api
```

## Optional profiles (never started by plain `up`)

| Profile | Service | Start | URL |
|---|---|---|---|
| `tools` | `kafka-ui` (`ghcr.io/kafbat/kafka-ui:v1.0.0`, read-only) | `make kafka-ui` | http://127.0.0.1:8080 |
| `monitoring` | `prometheus` (`prom/prometheus:v2.55.1`) with `infra/monitoring/alerts.yml` | `make monitoring` | http://127.0.0.1:9090 |
| `test` | `tests` (pytest on Linux, including NER and real-Kafka tests) | `make test-docker` | — |
| `test` | `spark-tests` (aggregation tests in the Spark image) | `make spark-tests` | — |

## Hardening applied

| Measure | Where applied |
|---|---|
| `no-new-privileges` and `cap_drop: ALL` | Every application, streaming and Kafka container |
| Read-only root filesystem | gateway, migrate, frontend, event-publisher, event-producer, spark-stream-processor, reporting-api, prometheus |
| Non-root users | gateway 10001, streaming 10002, reporting 10003, Spark 185, frontend 101, Kafka `appuser` |
| Least-privilege secrets | `event-publisher` receives only `DATABASE_URL`, not the whole `.env` file; `event-producer` receives no secrets; `reporting-api` receives only the JWT verification settings and the `reporting_ro` login |
| Graceful shutdown | `stop_grace_period`: SIGTERM, then the current batch finishes, the Kafka producer flushes, and the process exits |

## Volumes

| Volume | Used by | Contents |
|---|---|---|
| `pgdata` | postgres | All relational data incl. aggregates |
| `kafkadata` | kafka | Topic logs (size-capped per topic) |
| `sparkcheckpoints` | spark-stream-processor | Per-query offsets + RocksDB state - required for restart recovery |
| `sparkwork` | spark-stream-processor | Spark local dirs and JVM temp (RocksDB native library); disposable |
| `redisdata`, `ollamadata`, `promdata` | redis, ollama, prometheus | |

## Resource notes

Peak memory has not been measured yet. Measure it on the target machine with `docker stats` during `make verify-1m`.

Known contributors to memory use:
- `spark-stream-processor` is limited to 3 GB (driver heap 2 GB).
- Kafka heap is capped at 512 MB.
- `llama3.2:3b` in Ollama uses about 3 GB while loaded.
