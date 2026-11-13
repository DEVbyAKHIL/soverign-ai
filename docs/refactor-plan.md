# Refactor plan: Task 2 real-time streaming backend

**Title:** Sovereign AI Access Gateway: A Real-Time Streaming Data Backend for AI Governance, Security, and Operational Analytics

**Principle:** the gateway keeps every synchronous security check. It becomes the *event source*. A new data plane takes over from there:

Kafka → Spark Structured Streaming → PostgreSQL aggregate store → reporting API → dashboard

**Status:** a plan for review. No code has been written.

**Version pins:** every image tag below is a *candidate*. Each must be confirmed to exist and be pinned by digest in step 1 of `implementation-order.md`.

---

## 1. Files to modify

| File | Change |
|---|---|
| `docker-compose.yml` | Add `kafka`, `kafka-init`, `event-producer`, `spark-stream-processor`, `reporting-api`. Add health checks to every service, `restart: unless-stopped` on long-running services, and `stop_grace_period`. Move `ollama` to profile `model`. Add a dedicated `backend` network. New volumes: `kafkadata`, `sparkcheckpoints` |
| `docker-compose.prod.yml` | Resource limits for the new services |
| `.env.example` | `KAFKA_BOOTSTRAP`, `EVENTS_TOPIC_RAW`, `EVENT_PUBLISH_ENABLED`, `CALLER_ID_HASH_KEY`, `PRODUCER_MODE`, `PRODUCER_RATE`, `PRODUCER_TOTAL`, `PRODUCER_FAILURE_RATE`, `SPARK_DRIVER_MEMORY`, `REPORTING_DB_USER`/`PASSWORD`, `PROCESSOR_DB_USER`/`PASSWORD` |
| `Makefile` | Targets `stream-up`, `backfill`, `verify-1m`, `test-stream`, `topics` |
| `requirements.txt` | Add `aiokafka`, `jsonschema`, pinned with `==` |
| `gateway/config.py` | Kafka settings, `event_publish_enabled`, `caller_id_hash_key` (validated like the other keys) |
| `gateway/models.py` | `EventOutbox` model; aggregate-table models (read side for reporting-api) |
| `gateway/services/decisions.py` | Build the governance event and insert it into `event_outbox` before the commit at line 413. Same transaction, so no dual write |
| `gateway/services/forwarding.py` | Emit a `response_guardrail` event (outbox) after the output guard. `status=failed` on backend error |
| `gateway/main.py` | Start and stop the outbox relay in `lifespan`. Factor security middleware into `gateway/core/http.py` for reuse |
| `infra/docker/nginx.conf` | `location /reporting/` → `reporting-api:8001` (same-origin, so CORS stays strict) |
| `frontend/src/api/client.ts` | `reportingGet()` with a base path; polling hook |
| `frontend/src/App.tsx` | New routes and navigation |
| `frontend/src/pages/Dashboard.tsx` | Becomes "Real-time overview", backed by `/v1/metrics/overview` |
| `.github/workflows/ci.yml` | Build and Trivy-scan the new images; run streaming unit tests; lint new packages |
| `pyproject.toml` | Ruff/coverage include `streaming/`, `producer/`, `reporting/` |
| `README.md`, `ARCHITECTURE.md`, `SECURITY.md`, `EVALUATION.md`, `docs/api.md` | See §9 |

Gated behind a separate step, because it changes gateway behaviour (gap analysis C-2):

| File | Change |
|---|---|
| `gateway/services/decisions.py`, `policy/engine.py` inputs | Resolve `destination_model` and jurisdiction from the `ai_assets` registry, not the request body |

## 2. Files to create

```text
schemas/governance_event/1.0.0.json        # JSON Schema (draft 2020-12), additionalProperties: false
schemas/governance_event/README.md         # versioning rules (semver; additive = minor)

gateway/events/__init__.py
gateway/events/builder.py                  # Decision -> event dict (mapping table, HMAC caller_id)
gateway/events/outbox.py                   # insert + relay loop (aiokafka, idempotent producer)
gateway/core/http.py                       # shared headers / CORS / size-limit middleware

producer/__init__.py
producer/__main__.py                       # CLI: --mode continuous|backfill --total --rate --seed
producer/generator.py                      # tenants, agents, models, distributions (reuses ml/data/agents.py)
producer/failures.py                       # failure injection: bad schema, null ts, bad uuid, late, duplicate
producer/kafka_out.py                      # aiokafka producer, acks=all, idempotence, key=tenant_id
infra/docker/producer.Dockerfile

streaming/__init__.py
streaming/schema.py                        # Spark StructType mirrored from the JSON Schema + parity test
streaming/validate.py                      # parse, validate, route invalid -> dead-letter
streaming/enrich.py                        # derived fields
streaming/aggregations.py                  # one function per aggregation (pure DataFrame -> DataFrame)
streaming/sinks.py                         # foreachBatch Postgres upsert (staging table + ON CONFLICT)
streaming/progress.py                      # StreamingQueryListener -> pipeline_progress table + metrics
streaming/main.py                          # builds and starts all queries
infra/docker/spark.Dockerfile              # Spark base + Kafka connector + JDBC/psycopg, jars baked in with sha256

reporting/__init__.py
reporting/main.py                          # FastAPI app, middleware, /healthz /readyz /metrics
reporting/routers/metrics.py               # /v1/metrics/*
reporting/routers/agents.py                # /v1/agents/{agent_id}/risk
reporting/schemas.py                       # stable response models
reporting/queries.py                       # tenant-scoped SQLAlchemy Core queries
infra/docker/reporting.Dockerfile

migrations/versions/0006_streaming.py      # event_outbox, aggregate tables, pipeline_progress, DB roles/grants
infra/kafka/topics.sh                      # idempotent topic creation (kafka-topics --if-not-exists)
infra/monitoring/prometheus.yml            # scrape gateway, reporting-api, producer, processor
infra/monitoring/alerts.yml                # alert rules (see architecture doc §8)

scripts/verify_1m.py                       # the end-to-end performance / recovery verification

frontend/src/hooks/usePolling.ts           # interval + visibility pause; swappable for SSE later
frontend/src/pages/LiveOverview.tsx
frontend/src/pages/PolicyMetrics.tsx
frontend/src/pages/AgentRisk.tsx
frontend/src/pages/PiiMetrics.tsx
frontend/src/pages/InjectionMetrics.tsx
frontend/src/pages/LatencyReliability.tsx
frontend/src/pages/Sovereignty.tsx

tests/streaming/...                        # see §8
tests/reporting/...
tests/producer/...

DATA_FLOW.md, THREAT_MODEL.md
docs/kafka.md, docs/spark-streaming.md, docs/aggregations.md, docs/runbook.md
```

## 3. Docker Compose services to add

| Service | Image (candidate) | Responsibility | Health check | Depends on |
|---|---|---|---|---|
| `kafka` | `apache/kafka:3.8.1` (KRaft, single broker + controller) | Event backbone | `kafka-broker-api-versions.sh --bootstrap-server localhost:9092` | — |
| `kafka-init` | same image, one-shot | Create topics from `infra/kafka/topics.sh` | exits 0 | kafka healthy |
| `event-producer` | built `producer.Dockerfile` | Synthetic telemetry, continuous or backfill | heartbeat file freshness | kafka-init completed |
| `spark-stream-processor` | built `spark.Dockerfile` on `apache/spark:3.5.x` (Java 17, Python 3) | Validate, enrich, window, upsert | `/healthz` from a tiny HTTP thread that reports active queries | kafka-init, migrate |
| `reporting-api` | built `reporting.Dockerfile` | Serve aggregates | `GET /readyz` | migrate |
| `kafka-ui` (optional, profile `tools`) | `ghcr.io/kafbat/kafka-ui` pinned | Inspect topics for the demo | HTTP | kafka |
| `prometheus` (optional, profile `monitoring`) | `prom/prometheus` pinned | Scrape and alert | HTTP | — |

**No ZooKeeper:** Kafka 3.3+ runs KRaft in production mode, and ZooKeeper support is removed in Kafka 4.

**No schema registry:** the versioned JSON Schema file is the contract. A registry is noted as a later upgrade.

## 4. Kafka topics

| Topic | Partitions | Key | Retention | Producer | Consumer |
|---|---:|---|---|---|---|
| `governance.events.raw` | 6 | `tenant_id` | 7 days or 5 GB | gateway relay, event-producer | spark `validate` query (group via checkpoint) |
| `governance.events.valid` | 6 | `tenant_id` | 3 days | spark `validate` query | spark aggregation queries |
| `governance.events.dead-letter` | 1 | none | 14 days | spark `validate` query | operator / runbook, `verify_1m.py` counter |

Settings:
- `replication.factor=1`: single broker, documented as a local-only limitation.
- `min.insync.replicas=1`.
- `cleanup.policy=delete`.
- `compression.type=zstd` on producers.

## 5. Spark jobs (one application, separate streaming queries)

Each query has its own checkpoint directory under the `sparkcheckpoints` volume.

| Query | Source | Logic | Sink | Output mode |
|---|---|---|---|---|
| `q_validate` | `raw` | Parse JSON with `from_json` against the schema; check required fields, enums, UUIDs and the timestamp; `dropDuplicatesWithinWatermark(event_id)`; enrich | `valid` (Kafka) **and** `dead-letter` (Kafka). Two sinks via `foreachBatch` | append |
| `q_tumble_1m` | `valid` | Requests per tenant; requests per agent; decisions by type | `aggregated_request_metrics`, `aggregated_policy_metrics` | update → upsert |
| `q_slide_5m` | `valid` | Deny rate; average and P99 latency; injection attempts per agent; failed evaluations | `aggregated_policy_metrics`, `aggregated_latency_metrics`, `aggregated_injection_metrics` | update → upsert |
| `q_tumble_5m_pii` | `valid` | PII detections per model | `aggregated_pii_metrics` | update → upsert |
| `q_slide_10m_agent` | `valid` | Agent window features → anomaly score | `aggregated_agent_risk_metrics` | update → upsert |
| `q_tumble_10m_sov` | `valid` | Sensitive data volume by jurisdiction | `aggregated_sovereignty_metrics` | update → upsert |

Common settings:
- Watermark: `withWatermark("event_ts", "2 minutes")`.
- Trigger: `processingTime="5 seconds"`.
- `maxOffsetsPerTrigger` caps micro-batch size during backfill.
- State store: RocksDB provider.

## 6. PostgreSQL tables (migration `0006_streaming`)

Common columns on every aggregate table:

| Column | Notes |
|---|---|
| `window_start`, `window_end` | `timestamptz` |
| `window_type` | `tumbling` or `sliding` |
| `tenant_id` | |
| `metric` | text |
| `value` | numeric |
| `event_count` | bigint |
| `processed_at` | `timestamptz` |
| `batch_id` | bigint |
| `watermark` | `timestamptz` |

Each table's **primary key** is `(window_start, window_end, tenant_id, <dimension columns>, metric)`. An upsert therefore overwrites rather than duplicates.

| Table | Extra dimensions | Metrics |
|---|---|---|
| `aggregated_request_metrics` | `agent_id` (nullable → sentinel `'00000000-…'` for "all") | `requests`, `tokens` |
| `aggregated_policy_metrics` | `decision` (or `'*'`) | `count`, `deny_rate`, `failed_evaluations` |
| `aggregated_latency_metrics` | — | `avg_ms`, `p99_ms`, `max_ms` |
| `aggregated_pii_metrics` | `destination_model` | `pii_entities`, `events_with_pii` |
| `aggregated_injection_metrics` | `agent_id` | `attempts`, `avg_score`, `max_score` |
| `aggregated_agent_risk_metrics` | `agent_id` | `anomaly_score`, plus feature values |
| `aggregated_sovereignty_metrics` | `destination_jurisdiction`, `sensitivity_level` | `events`, `tokens`, `pii_entities` |

Supporting tables:

| Table | Purpose |
|---|---|
| `event_outbox` | `event_id` PK, `payload` jsonb, `created_at`, `published_at` null; partial index on unpublished rows |
| `pipeline_progress` | Per query and batch: input rows, processed rows, watermark, duration. Written by `StreamingQueryListener` |
| `ai_assets` | Already exists. Seeded for the registry step |

Indexes: `(tenant_id, window_start DESC)` on every aggregate table.

Roles:

| Role | Privileges |
|---|---|
| `processor_rw` | INSERT / UPDATE on aggregates and `pipeline_progress` only |
| `reporting_ro` | SELECT on aggregates and `pipeline_progress` only |
| gateway | Unchanged, plus `event_outbox` |

## 7. API endpoints (`reporting-api`, port 8001, proxied at `/reporting/`)

| Endpoint | Returns |
|---|---|
| `GET /v1/metrics/overview` | Latest-window KPIs: requests/min, deny rate, P99, PII events, injection attempts, top risky agents, pipeline lag |
| `GET /v1/metrics/requests` | Time series per tenant or agent (1-minute tumbling) |
| `GET /v1/metrics/policies` | Decisions by type; deny-rate series |
| `GET /v1/metrics/latency` | Average / P99 series; failed evaluations |
| `GET /v1/metrics/pii` | PII per model (5-minute) |
| `GET /v1/metrics/injections` | Attempts per agent (5-minute sliding) |
| `GET /v1/metrics/agents` | Latest anomaly score per agent, ranked |
| `GET /v1/metrics/sovereignty` | Volume by jurisdiction × sensitivity (10-minute) |
| `GET /v1/agents/{agent_id}/risk` | Score history for one agent (404 when the agent is not in the caller's tenant) |
| `GET /healthz`, `/readyz`, `/metrics` | Liveness, DB readiness, Prometheus |

Common query parameters:

| Parameter | Rules |
|---|---|
| `from`, `to` | ISO-8601. Maximum range 7 days; default the last 60 minutes |
| `agent_id` | Optional filter |
| `limit` | ≤ 1000 |
| `cursor` | Keyset pagination on `(window_start, pk)` |

Auth:
- Same JWT as the gateway, requiring the `audit:read` scope.
- **`tenant_id` always comes from the token, never from a query parameter.**

Every response carries `as_of` (latest `processed_at`) and `watermark`, so the dashboard can show freshness.

## 8. Tests to add

| File | Covers |
|---|---|
| `tests/streaming/test_schema.py` | JSON Schema accepts the sample and rejects missing, extra and wrong-enum fields; Spark StructType parity with the JSON Schema |
| `tests/streaming/test_validate.py` | Malformed JSON, null or non-UTC timestamps, bad UUIDs → dead-letter with a reason code |
| `tests/streaming/test_aggregations.py` | Window maths on a static DataFrame: tumbling boundaries, sliding overlap counts, deny rate, P99 against numpy |
| `tests/streaming/test_late_and_duplicates.py` | `MemoryStream`: late beyond the watermark is dropped and counted; duplicate `event_id` counted once |
| `tests/streaming/test_sink_upsert.py` | Re-running the same batch leaves row count and values unchanged |
| `tests/streaming/test_restart_recovery.py` | Run, stop, restart from checkpoint, same totals (local Spark + Postgres) |
| `tests/producer/test_generator.py` | Determinism by seed; distributions; failure-injection rates; timestamps UTC and bounded disorder |
| `tests/integration/test_event_outbox.py` | Decision → outbox row in the same transaction; rollback leaves no row; payload has no prompt, raw `caller_id` or entity values |
| `tests/integration/test_outbox_relay.py` | Relay publishes once, marks rows sent, retries on a broker error (mock producer) |
| `tests/reporting/test_metrics_api.py` | Each endpoint: auth, tenant isolation, time-range limits, pagination, schema stability (snapshot of the OpenAPI JSON) |
| `tests/e2e/test_kafka_flow.py` (profile `stream-test`) | Gateway request → raw → valid → aggregate row → reporting API |
| `scripts/verify_1m.py` | Backfill 1M, mid-run processor kill and restart, final reconciliation (see architecture doc §9) |

Streaming tests run in a `stream-tests` Compose service built from the Spark image (Java is required). The existing gateway suite is unchanged.

## 9. Documentation to update or create

| Document | Content |
|---|---|
| `README.md` | New title, architecture summary, quick start (`make stream-up`, `make backfill`, `make verify-1m`), resource requirements |
| `ARCHITECTURE.md` | Control plane (gateway) vs data plane (streaming); Mermaid diagrams; service matrix |
| `DATA_FLOW.md` | Ingestion → preprocessing → aggregation → storage → delivery, with field-level lineage |
| `SECURITY.md` | New services, Kafka exposure, DB roles, data minimisation in events |
| `THREAT_MODEL.md` | STRIDE per trust boundary (client → gateway, gateway → Kafka, Kafka → Spark, Spark → DB, API → browser) |
| `EVALUATION.md` | Measured 1M run: throughput, end-to-end lag, restart result. **Only numbers from real runs** |
| `docs/kafka.md` | Topics, keys, partitions, retention, delivery semantics, outbox |
| `docs/spark-streaming.md` | Queries, checkpoints, watermarks, triggers, state store, restart behaviour |
| `docs/aggregations.md` | Each aggregation: window type, size, slide, and why |
| `docs/runbook.md` | Start/stop, reset checkpoints, replay DLQ, consumer lag, disk full, broker down |
| `docs/api.md` | Reporting endpoints |
