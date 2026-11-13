# Implementation order

Each step ends with something runnable and tested, and is merged as its own PR. The streaming data plane comes first (steps 1–8). Changes to existing gateway behaviour come last (step 9). That way the 507 existing tests stay green throughout, and a submission is possible even if late steps slip.

**Effort scale:**

| Effort | Duration |
|---|---|
| S | ≤ ½ day |
| M | 1–2 days |
| L | 3+ days |

| Step | Deliverable | Depends on | Done when | Effort |
|---|---|---|---|---|
| 0 | **Review these planning docs**; confirm title, Kafka + Spark, and the open decisions below | — | User approval | — |
| 1 | **Infra skeleton:** `kafka` (KRaft) + `kafka-init` + `infra/kafka/topics.sh`; pin image tags and digests; `ollama` → profile `model`; health checks and restart policies on all services; resource budget check | 0 | `docker compose up` → topics listed with 6/6/1 partitions; `docker stats` peak memory recorded | S |
| 2 | **Event contract:** `schemas/governance_event/1.0.0.json`, sample events, validation tests, mapping table from decision actions | 0 | `tests/streaming/test_schema.py` green | S |
| 3 | **event-producer:** generator (reusing `ml/data/agents.py`), continuous and backfill modes, failure injection, idempotent Kafka producer, Dockerfile | 1, 2 | 10k-event backfill visible on `raw`; generator tests green | M |
| 4 | **Migration 0006:** `event_outbox`, seven aggregate tables, `pipeline_progress`, `processor_rw` / `reporting_ro` roles | 0 | `alembic upgrade head` + downgrade round trip; grants tested | S |
| 5 | **Spark image + `q_validate`:** connector jars baked in with sha256; parse/validate/dedup/enrich; raw → valid + dead-letter; checkpoints on a volume; progress listener | 1–4 | Injected bad events land in DLQ with reasons; valid count = produced − invalid | M |
| 6 | **Aggregation queries + upsert sink:** five window specs, `foreachBatch` upsert. **Spike first:** `percentile_approx` in streaming and anomaly-model feature parity | 5 | Window-maths tests and upsert-idempotency tests green; rows appear in all seven tables | L |
| 7 | **reporting-api:** endpoints, JWT/tenant scoping, pagination, OpenAPI, shared middleware, Dockerfile, nginx `/reporting/` route | 4 (6 for real data) | `tests/reporting` green; `/docs` renders | M |
| 8 | **1M verification + restart test:** `scripts/verify_1m.py`, `make verify-1m`; record a real run in `EVALUATION.md` | 3, 5, 6 | Script prints the summary and exits 0 on a real run. **Numbers recorded only from that run** | M |
| 9 | **Gateway as event source:** outbox insert in `decide()` and `forward()`, relay in lifespan, `caller_id` HMAC, metadata allow-list | 2, 4 | Existing suite still green; new outbox tests green; a live request appears in `aggregated_request_metrics` | M |
| 10 | **Trusted registry:** resolve model and jurisdiction from `ai_assets`; seed registry; update affected tests and docs | 9 | Unknown model → `unknown` → default deny; a spoofed `destination_jurisdiction` in the body has no effect | M |
| 11 | **Dashboard:** `usePolling`, seven metric pages, overview rewired, stale-data banner | 7 | `npm run build`; pages render against live data; no raw values shown | M |
| 12 | **Observability & alerts:** Prometheus config, alert rules, metrics in producer/processor/reporting | 5–7 | `promtool check rules` passes; lag metric visible | S |
| 13 | **CI:** build and scan new images; run streaming tests in the Spark test image | 3–7 | CI green on PR | S |
| 14 | **Documentation set:** README, ARCHITECTURE, DATA_FLOW, SECURITY, THREAT_MODEL, EVALUATION, docs/kafka, spark-streaming, aggregations, runbook, api | All | Every doc reflects the code as merged; no unmeasured claims | M |

**Critical path:** 1 → 2 → 3 → 5 → 6 → 8. These steps alone satisfy the 1M streaming requirement. Steps 9–10 make the gateway's own traffic part of the stream and close the trust gap. Steps 11–14 are delivery and polish.

**Branching:**
- One branch per step: `feat/stream-01-kafka`, and so on.
- Each PR ends with a passing local run of the relevant tests.
- No step changes Git history.

## Open decisions for the reviewer

1. **Spark vs a lighter equivalent.** The brief allows "or an equivalent". Spark is the plan, but it is the biggest memory and image cost (about 1–1.5 GB image; driver ≥ 2 GB). Keep Spark?
2. **Outbox relay placement.** Planned as a background task inside the gateway process (simplest). The alternative is a separate `outbox-relay` service (more isolated, one more container).
3. **Step 10 (registry) changes gateway behaviour** and some existing tests. Confirm it is in scope.
4. **Resource budget:** 15.8 GB RAM and 33 GB free on C:. Confirm Docker Desktop's disk image location and the memory limit allotted to WSL2 before step 1.
5. **Optional services:** include `kafka-ui` and `prometheus` under opt-in profiles, or leave them out?
