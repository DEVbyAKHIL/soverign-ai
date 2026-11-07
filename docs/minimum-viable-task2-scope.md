# Minimum viable Task 2 scope

**Purpose:** the smallest scope that satisfies Task 2 and can be defended in the portfolio.

**Status markers:**

| Marker | Meaning |
|---|---|
| ✅ | Implemented and verified on 2026-11-07 |
| ⚠️ | Implemented with a gap |
| ❌ | Missing |

## Must-have (required for a compliant submission)

| Item | State | Evidence / remaining work |
|---|---|---|
| Kafka as the ingestion and buffering layer (KRaft, 3 topics, partitioned) | ✅ | `docker-compose.yml:100-143`, `infra/kafka/topics.sh` |
| Event producer (continuous + backfill, configurable rate, timestamps, event IDs) | ✅ | `producer/` |
| ≥ 1,000,000 timestamped events processed end to end, with a runnable verification | ✅ | `scripts/verify_pipeline.py`; `EVALUATION.md` run 2. Re-run before submission and keep the log |
| Spark Structured Streaming job | ✅ | `streaming/jobs.py`, `infra/docker/spark.Dockerfile` |
| ≥ 6 meaningful aggregations, tumbling + sliding, watermark | ✅ | 8 queries / 11 metrics, `docs/aggregations.md` |
| PostgreSQL aggregate storage with idempotent writes | ✅ | Migration 0007, `streaming/sink.py` |
| Real-time reporting API | ⚠️ | `reporting/`. **Commit and push** |
| Dashboard integration | ⚠️ | `frontend/src/pages/live/`. **Commit and push** |
| Checkpointing and restart recovery | ✅ | `sparkcheckpoints` volume; restart verified |
| Dead-letter handling | ✅ | `streaming/jobs.py` `validation_query` |
| End-to-end verification | ✅ | `verify_pipeline.py` |
| Docker Compose, isolated services, IaC | ✅ | `docker-compose.yml`, Dockerfiles, migrations |
| Code on GitHub (the graded artefact) | ❌ | Remediation C1 |
| Architecture PNG plus phase documents, link `.txt`, 2-page PDF | ❌ | C2-C5 (portfolio form, not code) |
| Reproducible setup for the examiner | ⚠️ | H1 (`init_env.py` plus a clean-clone test) |

## Should-have (strengthens the reliability, scalability, security and governance criteria)

| Item | Remediation |
|---|---|
| Retention enforced (outbox, aggregates; audit policy documented) | H3 |
| Keyed HMAC instead of plain SHA-256 fingerprints | H4 |
| Least-privilege publisher DB role | H6 |
| CI runs the Kafka tests and scans all images and requirements files | H2 |
| Updated SECURITY.md and privacy.md, new THREAT_MODEL.md, stale plans marked historical | H7, H8 |

## Nice-to-have

| Item | Remediation |
|---|---|
| Trusted model registry (Phase 4) | H5. Important for the AI-governance story, **not** a Task 2 requirement |
| Scaling experiment | M1 |
| API check inside the end-to-end script | M2 |
| Streaming alerts and scrape targets | M3 |
| Digest-pinned images | M4 |
| Canary and cross-tenant output checks | M5 |

## Can be deferred (beyond Task 2)

- Multi-broker Kafka with TLS/SASL and ACLs.
- Spark cluster mode.
- A schema registry with Avro.
- SSE or WebSocket push to the dashboard (the polling hook is already swappable).
- Phase 8 hardening, OIDC, the Isolation Forest scoring stream windows.
- Distributed tracing.

**Conclusion:** the minimum viable Task 2 scope is technically complete. The must-have work that remains is delivery and portfolio work, not new features:
1. Commit, push and merge (C1).
2. The reproducibility helper and clean-clone test (H1).
3. The portfolio artefacts (C2-C5).
