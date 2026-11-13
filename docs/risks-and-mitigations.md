# Risks and mitigations

**Likelihood (L) and impact (I) scale:** H = high, M = medium, L = low.

## Delivery and technical risks

| ID | Risk | L | I | Mitigation | Detection / owner step |
|---|---|---|---|---|---|
| R-01 | **Host resources:** Kafka + Spark + Postgres + frontend build exceed the available 15.8 GB RAM or 33 GB free on C:. Docker Desktop crashed once already in this project | M | H | Ollama moved to an opt-in profile; Spark driver memory capped (`SPARK_DRIVER_MEMORY=2g`); Kafka heap 512 MB; `maxOffsetsPerTrigger` limits micro-batches; measure with `docker stats` in step 1 before building further | Step 1 budget check |
| R-02 | **Spark image and connector supply chain:** large image, many JVM transitive dependencies, connector jars fetched from Maven | H | M | Pin image by digest; download jars at build time with sha256 checks; no runtime `--packages`; Trivy scan with an explicit, reviewed ignore file | Step 5, CI step 13 |
| R-03 | **Dual write** between Postgres and Kafka loses or invents events | H (if naive) | H | Transactional outbox; relay with `FOR UPDATE SKIP LOCKED`; dedup by `event_id` downstream | Outbox tests, step 9 |
| R-04 | **Late events dropped by the watermark** make the 1M reconciliation fail | M | M | Backfill disorder ≤ 60 s < 2-min watermark; late events counted (`late_events_total`) and reported, never silently lost; a separate test proves late handling | Step 8 |
| R-05 | **P99 in streaming aggregation** not supported, or too expensive in state | M | M | Spike first in step 6; fallback to latency histogram buckets plus P99 at read time | Step 6 spike |
| R-06 | **Agent anomaly model feature mismatch:** the existing Isolation Forest uses a 7-day baseline unavailable inside a 10-minute stream window | H | M | Confirm parity in the spike. If it doesn't hold, use a documented z-score against a rolling per-agent baseline table and **label it honestly** in the docs | Step 6 spike |
| R-07 | **Sliding-window state growth** (5- and 10-minute windows, 1-minute slide = 5–10× rows per event) under backfill | M | M | RocksDB state store; watermark-driven state cleanup; `update` output mode; state metrics in `pipeline_progress` | Step 6, step 8 run |
| R-08 | **Upsert contention:** several queries write `aggregated_policy_metrics` | L | M | Disjoint `metric` values per query, so primary keys never collide across queries; batched staging table + single `INSERT … ON CONFLICT` per batch | Sink tests |
| R-09 | **Backfill throughput too slow on a laptop** to process 1M in reasonable time | M | M | Raise `maxOffsetsPerTrigger`; 6 partitions → 6 tasks; measure and report real throughput; the requirement is volume, not a fixed speed | Step 8 |
| R-10 | **Scope creep** (registry, dashboard pages, Prometheus) delays the critical path | M | H | Critical path 1→2→3→5→6→8 first; later steps are independent PRs | Implementation order |
| R-11 | **Regression in existing gateway behaviour** | M | H | Event publishing is additive and behind `EVENT_PUBLISH_ENABLED`; registry change isolated in step 10; full existing suite in every PR | CI |
| R-12 | **Spark tests need Java**, so they don't run on the Windows host venv | H | L | Run them in a `stream-tests` Compose service built from the Spark image; keep pure-Python logic (schema, generator, mapping) host-testable | Step 5, 13 |
| R-13 | **Unverified claims in docs** (throughput, fault tolerance) | M | H | `EVALUATION.md` takes numbers only from `verify_1m.py` output; words like "production-ready" or "scalable" only with evidence | Step 14 review |

## Security, governance and data-protection risks introduced by the refactor

| ID | Risk | L | I | Mitigation |
|---|---|---|---|---|
| S-01 | **Kafka without auth/TLS:** any container on the network can read or write topics | M | H | Kafka on an internal network with no host port by default; only gateway, producer and processor attached to it; TLS/SASL settings documented as TLS-ready options; listed as a local-only limitation in `SECURITY.md` |
| S-02 | **Sensitive data leaking into events** (prompts, entity values, user ids) | M | H | Schema `additionalProperties: false`; no free-text fields except bounded enums/ids; `metadata` key allow-list; `caller_id` HMAC; test asserting the event built from a PII request contains no entity values |
| S-03 | **DLQ holds arbitrary malformed payloads**, possibly containing whatever a buggy producer sent | M | M | Truncate to 16 KB; 14-day retention; DLQ never shown in the dashboard; runbook explains inspection under the same access rules |
| S-04 | **Over-privileged DB access** for the new services | M | H | `processor_rw` (INSERT/UPDATE on aggregates only), `reporting_ro` (SELECT only); passwords in `.env`; grants tested in the migration test |
| S-05 | **Tenant leakage via the reporting API** | L | H | Tenant from JWT only; every query goes through a tenant-scoped helper; cross-tenant tests for every endpoint |
| S-06 | **Re-identification from small aggregates** (for example 1 PII event for 1 agent in 5 min) | M | M | Small-count suppression (`<5`) on PII and sovereignty endpoints; aggregates never include entity values |
| S-07 | **Event spoofing:** a compromised component writes forged events to `raw` | L | M | Only gateway and producer can reach Kafka; `source` header; producer events carry `metadata.synthetic=true` so reports can separate real from synthetic traffic |
| S-08 | **Client-supplied jurisdiction/model** (existing, now in scope) | H | H | Step 10: resolve from the `ai_assets` registry; unknown → default deny |
| S-09 | **More attack surface:** new HTTP endpoints, new images | M | M | Shared security middleware; non-root; read-only root FS where the image allows (Spark needs writable `/tmp` and the checkpoint volume only); Trivy + pip-audit in CI |
| S-10 | **Checkpoint volume tampering** could corrupt offsets or state | L | M | Volume mounted only into the processor; recovery path (recompute from Kafka within retention) documented |
| S-11 | **Synthetic data mistaken for real evidence** in the portfolio | M | M | Label producer events `synthetic`; documentation states clearly which numbers come from synthetic backfill |
| S-12 | **Retention of governance telemetry** longer than needed | L | M | Topic retention 3–14 days; aggregate retention policy (for example 90 days) documented in `docs/privacy.md`; raw events not stored in Postgres by the processor |
