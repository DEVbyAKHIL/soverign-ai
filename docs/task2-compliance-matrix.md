# Task 2 compliance matrix

**Task 2:** "Build a real-time data backend for a data-intensive application." Audit date: 2026-11-07.

**Sources:** the code on branch `feat/stream-phase1`, plus the S3 working tree (uncommitted), plus runs recorded on this machine.

**Scale:**

| Status | Meaning |
|---|---|
| PASS | Code plus test or recorded run |
| PARTIAL | Implemented with a gap that a reviewer would notice |
| FAIL | Missing |

Full evidence and remediation per item: `docs/compliance-audit.md`.

| # | Task 2 requirement | Status | Implemented by (file:line) | Verified by | Remaining gap |
|---|---|---|---|---|---|
| 1 | Real-time backend (not just a synchronous API) | **PASS** | `streaming/jobs.py:100-153`; `docker-compose.yml:165,189` | 1M end-to-end run (`EVALUATION.md`) | - |
| 2 | Continuous ingestion | **PASS** | `producer/run.py` (`continuous`, `backfill --live`); gateway outbox `gateway/services/decisions.py:471-487`; `publisher/service.py:60-115` | `tests/streaming/test_producer.py`; `tests/integration/test_outbox_publisher.py`; live gateway → Kafka trace | - |
| 3 | ≥ 1,000,000 timestamped events ingested, processed, aggregated | **PASS** | `scripts/verify_pipeline.py`; `producer/__main__.py verify-1m` | `EVALUATION.md`: run 2 = 1,000,000 produced / consumed / aggregated, 0 dead-letter, 0 late, restart at 430,000 | One recorded run on one machine. Re-run before submission and keep the log |
| 4 | Kafka ingestion and buffering | **PASS** | `docker-compose.yml:100-143` (KRaft); `infra/kafka/topics.sh`; `publisher/sender.py:25-33` (`acks=all`, idempotent); key = `tenant_id` | `tests/integration/test_kafka_roundtrip.py` (real broker, Docker) | No Kafka in CI, so these tests skip on GitHub |
| 5 | Spark Structured Streaming | **PASS** | `infra/docker/spark.Dockerfile` (3.5.8); `streaming/jobs.py` (Kafka source; checkpoint `:120,:150`; trigger `:121,:151`) | `tests/spark/test_aggregations.py` (11) plus the end-to-end run | - |
| 6 | Preprocessing | **PASS** | `streaming/validation.py` (schema, null, malformed, non-UTC); `streaming/aggregations.py` `enrich()`; dead-letter in `validation_query` | `tests/streaming/test_processor_units.py` | - |
| 7 | Aggregation (≥ 6 meaningful) | **PASS** | `streaming/aggregations.py` SPECS: 8 queries, 11 metrics | Spark tests; aggregate rows from the 1M run | - |
| 8 | Windowing (tumbling + sliding, watermark, justified) | **PASS** | `streaming/aggregations.py:66-75`; `docs/aggregations.md` | Spark window-maths tests; streaming dedup test | - |
| 9 | Storage of aggregates | **PASS** | Migration `0007` (PKs, indexes, checks); `streaming/sink.py` upserts | `tests/integration/test_aggregate_sink.py` | - |
| 10 | Real-time delivery (API) | **PASS** | `reporting/main.py:152-245`; `reporting/queries.py` | `tests/reporting/test_reporting_api.py` (11); live smoke test | **Uncommitted** (phase S3) |
| 11 | Dashboard / report | **PASS** | `frontend/src/pages/live/*` (7 pages); `usePolling` 5 s | Browser check of 3 pages; `tsc` build | **Uncommitted**; no automated UI test |
| 12 | Reliability | **PASS** | Health checks, retries, DLQ, checkpoints, graceful shutdown (see `compliance-audit.md` T2-05) | Restart-recovery run; publisher retry tests | No streaming alert rules |
| 13 | Scalability | **PARTIAL** | Partitions, SKIP LOCKED, indexes, prod replicas | - | Single broker (RF 1), Spark local mode, no scaling test, uneven tenant→partition spread |
| 14 | Maintainability | **PASS** | Modular packages, CI, pinned deps, 723 tests | Docker test run | - |
| 15 | Security | **PARTIAL** | JWT/RBAC, tenant scoping, least-privilege DB roles, headers/CORS/limits, scans | Security and role tests | Plaintext Kafka/Postgres; only the gateway image and `requirements.txt` scanned; publisher uses the app DB login; registry trust gap (A-02) |
| 16 | Governance | **PARTIAL** | JSON Schema contract; lineage (`DATA_FLOW.md`); hash-chained audit; classification | Schema and audit tests | Retention not enforced (`AUDIT_RETENTION_DAYS` unused; outbox and aggregates unbounded); ownership undocumented |
| 17 | Protection | **PARTIAL** | Value-free events; HMAC `caller_id`; payload-free DLQ; small-cell suppression | `test_outbox_payload_carries_no_sensitive_values` | Plain SHA-256 content/output fingerprints (`gateway/services/decisions.py:198`, `forwarding.py:43`); no deletion |
| 18 | Docker microservices | **PASS** (Docker usage itself PARTIAL) | 5 Dockerfiles; separate services | `docker compose ps`: all healthy | Base images pinned by tag, not digest; Trivy scans only the gateway |
| 19 | Infrastructure as Code | **PASS** | Compose, topics script, migrations 0001-0008, `.env.example`, monitoring config | Stack built from these files | - |
| 20 | Local deployment | **PARTIAL** | One `docker compose up --build -d`; `migrate` automatic; runbook | Works on this machine | No clean-clone test; manual `.env` (7 secrets); `make` not on Windows; Ollama model pull |

## Verdict for Task 2

**The technical requirements are met.**
- Items 1-12, 14 and 19 pass.
- Items 13 and 15-17 are partial, as quality gaps rather than missing functionality.
- Item 18 passes for the microservice split; Docker usage itself is partial (tag-pinned images, gateway-only scanning).
- Item 20 is partial: it works on this machine but hasn't been tested from a clean clone.

**What stops it from counting as delivered:**
1. Phase S3 is uncommitted.
2. Nothing streaming-related has been pushed to GitHub.
3. The portfolio artefacts are missing: architecture PNG, phase texts, link `.txt`, abstract PDF.
