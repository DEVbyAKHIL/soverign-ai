# Task 2 gap analysis

**Scope:** the repository at commit `530252d` (main), compared against the 14 Task 2 requirements, the required services, Kafka design, event schema, aggregations, reliability, security and testing sections of the refactor brief.

**Method:** static inspection only. No containers were started and no tests were run to produce this document. Test results quoted below come from earlier runs in the development session: 507 passed on the host; 506 passed + 1 skipped on Linux with 97.46% coverage. They are labelled as such.

**Status labels:**

| Label | Meaning |
|---|---|
| **MET** | Implemented and verified by tests |
| **PARTIAL** | Exists but does not satisfy the requirement as written |
| **MISSING** | Not present |
| **CONFLICT** | Existing behaviour contradicts the new requirement |

---

## 1. Summary

The repository is a synchronous security gateway with a good control plane:
- JWT and RBAC
- tenant isolation
- policy-as-code
- PII and injection detection
- a hash-chained audit trail
- output guardrails
- a React dashboard
- Docker Compose
- CI security scanning

It has **no streaming data plane**: no broker, no stream processor, no persisted window aggregates and no high-volume dataset.

The gateway already computes almost every field the event schema needs, so it can become the event source with a small change. **Everything downstream of the gateway is new work.**

---

## 2. Requirement-by-requirement comparison

### 2.1 Task 2 compliance requirements

| # | Requirement | Status | Evidence | Gap |
|---|---|---|---|---|
| 1 | Continuously ingest a large volume of timestamped data | MISSING | Ingestion is per HTTP request only (`gateway/routers/infer.py`) | Need a continuous event source and Kafka ingestion |
| 2 | Ingest, process and aggregate ≥ 1,000,000 timestamped points | MISSING | Largest dataset is `ml/data/external/deepset_*` (about 662 rows); generators in `ml/data/agents.py:98,182,197` are in-memory and small | Need a backfill producer plus end-to-end counts |
| 3 | Real-time streaming architecture | MISSING | `decide()` is synchronous: detect, then policy, then insert, then commit (`gateway/services/decisions.py:204-413`) | Add the Kafka → Spark → Postgres → API path |
| 4 | Kafka as the ingestion/buffering layer | MISSING | No Kafka client or service anywhere; Redis is used only for rate limiting (`gateway/core/rate_limit.py`) | New `kafka` service, topics and producers |
| 5 | Spark Structured Streaming (or equivalent) | MISSING | None | New `spark-stream-processor` service |
| 6 | Meaningful time-windowed aggregations | PARTIAL | 10-min / 7-day feature windows computed per request (`agents/features.py:12-13`); per-day `GROUP BY` at read time (`gateway/routers/dashboard.py:87`) | Not stream windows, not persisted, no watermark |
| 7 | Store aggregated results in PostgreSQL | MISSING | Postgres stores raw decisions and events only (`gateway/models.py`) | Seven `aggregated_*` tables (migration `0006`) |
| 8 | Deliver continuously updated results via an API | PARTIAL | `GET /v1/dashboard/summary` aggregates raw tables on each request (`dashboard.py:71`) | New `reporting-api` reading the aggregate tables |
| 9 | Docker Compose for all services, locally | PARTIAL | `docker-compose.yml` has postgres, redis, migrate, gateway, ollama, frontend and tests | Add kafka, kafka-init, event-producer, spark-stream-processor, reporting-api |
| 10 | Isolated microservices | PARTIAL | Separate containers exist, but all application logic is in `gateway` | Producer, processor and reporting API each need their own service |
| 11 | Infrastructure as Code | PARTIAL | Compose, Dockerfiles (`infra/docker/*`), Alembic migrations, `.env.example` | Topic creation, Spark config and alert rules must also be declarative |
| 12 | Reliability, scalability, maintainability, security, governance | PARTIAL | Security strong (see 2.7); reliability partial (healthchecks on postgres/redis only, `docker-compose.yml:17-21,29-33`); scalability only claimed (`ARCHITECTURE.md:13`, `docker-compose.prod.yml:14`) | Streaming reliability semantics; measurements |
| 13 | Demonstrate end-to-end processing of ≥ 1M events | MISSING | — | Verification script plus a recorded run |
| 14 | Document the full data flow | MISSING | `ARCHITECTURE.md` documents the request path only | `DATA_FLOW.md` and the docs set (see refactor plan §9) |

### 2.2 Required services

| Service | Status | Notes |
|---|---|---|
| gateway | MET | Must gain an event publisher (outbox + relay) |
| event-producer | MISSING | New |
| kafka | MISSING | New. **KRaft mode, so no ZooKeeper** (see architecture doc §4) |
| zookeeper | NOT REQUIRED | KRaft removes it |
| schema-registry (optional) | NOT PLANNED | A versioned JSON Schema file in the repo, shared by all services, is enough for the MVP. Avro + registry is noted as a later upgrade |
| spark-stream-processor | MISSING | New |
| postgres | MET | Gains migration `0006`, a read-only role and a processor role |
| redis | MET | Unchanged (rate limiting) |
| reporting-api | MISSING | New |
| frontend | MET | New pages; reads from the reporting API |
| kafka-ui (optional) | NOT PLANNED by default | Can be offered under a `tools` profile |

### 2.3 Kafka design

| Item | Status |
|---|---|
| `governance.events.raw` (≥ 6 partitions) | MISSING |
| `governance.events.valid` | MISSING |
| `governance.events.dead-letter` | MISSING |
| Gateway publishes after every policy decision | MISSING |
| Partition key (`tenant_id`) | MISSING |
| Consumer groups / checkpointing | MISSING |

### 2.4 Event schema

The gateway already holds every required field except where noted below. Sources are in `gateway/services/decisions.py` and `gateway/models.py`.

| Field | Current source | Status |
|---|---|---|
| `event_id` | `Decision.id` (UUID) | Reusable |
| `event_timestamp` | `Decision.created_at` (DB `now()`) | Reusable. Must be emitted as UTC ISO-8601 |
| `tenant_id`, `agent_id`, `caller_type`, `caller_id` | `principal`, `req` | Reusable. **`caller_id` is a user identifier, so it should be pseudonymised (keyed HMAC) before leaving the gateway** |
| `decision` | `Decision.action_taken`: the winning action from `policy/engine.py:19-28` | Needs a mapping, see the table below |
| `destination_model`, `destination_jurisdiction` | `req.requested_model`, `req.destination_jurisdiction` | **CONFLICT:** client-supplied, see 2.7 |
| `declared_purpose` | `req.declared_purpose` | Reusable |
| `sensitivity_level` | `req.data_classification` combined with the highest detected risk | Derivable |
| `pii_count` | Number of detections | Derivable |
| `injection_risk_score` | `Decision.input_risk_score` / intent confidence | Reusable |
| `policy_ids` | Matched policies | Reusable |
| `latency_ms` | `AgentEvent.latency_ms` (computed in `decide`) | Reusable |
| `tokens_processed` | `AgentEvent.tokens_processed` | Reusable |
| `status` | — | New: `blocked` for deny/quarantine; `failed` for internal or forwarding errors; otherwise `completed` |
| `metadata` | — | New. **Keys must be allow-listed** so the field cannot become a channel for sensitive values |
| `schema_version` | — | New |

**Decision vocabulary mapping.** The brief uses action-level values. The engine precedence (`policy/engine.py:19-41`) maps one to one:

| Engine action | Event `decision` |
|---|---|
| `deny` | `deny` |
| `quarantine` | `quarantine` |
| `require_human_review` | `require_review` |
| `route_to_approved_model` | `route` |
| `redact`, `pseudonymise`, `mask` | `redact` |
| `allow` | `allow` |

The existing `final_decision` (allow / allow_with_modification / deny / review) stays in Postgres unchanged.

### 2.5 Aggregations

None of the eleven required aggregations exist as stream windows. The closest existing code is `agents/features.py` (10-minute window features) and `agents/anomaly_detector.py` (Isolation Forest), which can be reused for the agent anomaly score; see the risk register, R-06.

### 2.6 Reliability requirements

| Requirement | Status | Evidence / gap |
|---|---|---|
| Consumer groups | MISSING | — |
| At-least-once semantics | MISSING | — |
| Idempotent aggregation writes | MISSING | — |
| Spark checkpointing | MISSING | — |
| Dead-letter topic | MISSING | — |
| Retry for transient failures | PARTIAL | Forwarding failures are recorded, not retried (`gateway/services/forwarding.py`) |
| Health checks for every service | PARTIAL | postgres, redis, gateway image `HEALTHCHECK`; **none on ollama or frontend** |
| Graceful shutdown | PARTIAL | Uvicorn default; nothing for stream consumers |
| Restart policies | PARTIAL | Only in `docker-compose.prod.yml` |
| Structured logs | MET | structlog (`gateway/logging_config.py`) |
| Prometheus metrics | MET (gateway only) | `/metrics`; `gateway_model_calls_total` and others in `forwarding.py` |
| Basic alerting rules | MISSING | `infra/monitoring/` is empty |

### 2.7 Security requirements

| Requirement | Status | Evidence / gap |
|---|---|---|
| JWT auth, RBAC | MET | `gateway/core/security.py`, `gateway/dependencies.py`; tests in `tests/unit/test_security.py` |
| Tenant isolation | MET | `gateway/core/tenant.py`; body `tenant_id` must equal the token tenant (`decisions.py:97`) |
| Least-privilege service accounts | PARTIAL | Containers are non-root with `cap_drop: ALL`; **a single DB role is shared by migrate and gateway** |
| Secrets from env; none in Git | MET | `.env` gitignored; `.env.example` only |
| TLS-ready config | PARTIAL | HSTS header added when not in dev (`gateway/main.py:113`); no TLS listener config. Kafka would be PLAINTEXT on the internal network |
| Secure headers, strict CORS | MET | `gateway/main.py:31,67`; wildcard rejected (`gateway/config.py:61-65`) |
| Rate limiting, request-size limits | MET | `gateway/core/rate_limit.py`; content-length check (`gateway/main.py:85`) |
| Input validation, SQL-injection prevention | MET | Pydantic `Literal` types (`gateway/schemas/requests.py`); SQLAlchemy bound parameters; test for `review_status=x;drop` (`tests/integration/test_dashboard.py:64`) |
| SSRF prevention / outbound allow-list | PARTIAL | Requests go only to the configured `MODEL_BACKEND_URL`; `requested_endpoint` is not dialled. There is no allow-list registry |
| **Do not trust client-supplied endpoint, jurisdiction, tenant** | **CONFLICT** | Tenant: met. **Jurisdiction and model are taken from the request body** (`decisions.py:144,276`) and drive the sovereignty policy. An `AIAsset` registry table with a `jurisdiction` column exists (`gateway/models.py:71-86`) but `decide()` does not use it |
| Non-root containers, image + dependency scanning | MET | `USER 10001` / `USER 101`; Trivy, pip-audit, bandit, semgrep in `.github/workflows/ci.yml` |
| Structured audit logging | MET | Hash-chained `audit_events` (`audit/service.py`) |
| No raw sensitive data in Kafka, logs, analytics, frontend | MET for the existing paths (prompts never stored; entity values hashed) | **Must be enforced for the new paths:** the event schema forbids prompts; `metadata` allow-list; `caller_id` pseudonymised |

### 2.8 Testing requirements

| Area | Status |
|---|---|
| Gateway tests (auth, RBAC, tenant, policy, PII, injection, audit, guardrails, rate limit, validation) | MET (`tests/unit`, `tests/integration`, `tests/security`) |
| Streaming tests (schema, DLQ, consumption, timestamps, windows, upserts, restart, 1M, E2E, reporting API, dashboard integration) | MISSING |
| Performance / 1M verification script | MISSING |

---

## 3. Reusable files

| File | Reuse |
|---|---|
| `gateway/services/decisions.py` | Source of every event field. Add the outbox insert inside the same transaction (before the commit at line 413) |
| `gateway/core/security.py` | JWT decoding for `reporting-api` (import, do not duplicate) |
| `gateway/core/tenant.py` pattern | Tenant scoping in reporting-api queries |
| `gateway/logging_config.py` | Shared structlog setup for the new Python services |
| `gateway/main.py` middleware (headers, CORS, size limit) | Factor out so reporting-api applies the same middleware |
| `ml/data/agents.py` (`profile`, `_event`, `ANOMALY_TYPES`, Poisson rates) | Realistic normal and anomalous agent behaviour in event-producer |
| `ml/data/injection.py` | Label distribution for synthetic injection scores |
| `agents/anomaly_detector.py`, `agents/models/` | Candidate scorer for the agent anomaly aggregate (needs a feature-parity check) |
| `policy/policies/*.yaml` | Realistic policy ids and decision mix for the producer |
| `infra/docker/gateway.Dockerfile` | Base pattern (non-root, multi-stage, healthcheck) for the producer and reporting-api images |
| `frontend/src/api/client.ts`, `components/ui.tsx` | Fetch wrapper and UI kit for the new pages |
| `.github/workflows/ci.yml` | Extend with new images, Trivy scans and tests |

## 4. Files that must be modified

See `refactor-plan.md` §1 for the exact list. In short:
- `docker-compose.yml`, `.env.example`, `Makefile`
- `gateway/config.py`, `gateway/main.py`, `gateway/models.py`, `gateway/services/decisions.py`, `gateway/services/forwarding.py`
- `requirements.txt`
- `infra/docker/nginx.conf`
- `frontend/src/{App.tsx, api/client.ts, pages/Dashboard.tsx}`
- `.github/workflows/ci.yml`
- `README.md`, `ARCHITECTURE.md`, `SECURITY.md`, `EVALUATION.md`, `docs/api.md`

## 5. New services

`kafka`, `kafka-init` (one-shot topic creation), `event-producer`, `spark-stream-processor`, `reporting-api`. Optional: `kafka-ui` (profile `tools`), `prometheus` (profile `monitoring`).

## 6. Architectural conflicts

| ID | Conflict | Resolution proposed |
|---|---|---|
| C-1 | **Dual write.** The gateway commits to Postgres and must also publish to Kafka. A crash between the two loses or invents events | **Transactional outbox:** insert the event into `event_outbox` in the same transaction as the decision; a relay publishes and marks rows sent. At-least-once delivery, deduplicated downstream by `event_id` |
| C-2 | **Client-supplied jurisdiction and model** versus the "do not trust" requirement | Resolve them from the `ai_assets` registry (`AIAsset`). Unknown model → `jurisdiction=unknown` → existing default-deny. **This changes gateway behaviour and existing tests**, so it is a separate step (see implementation order, step 9) |
| C-3 | **Two decision vocabularies** (`final_decision` vs the brief's `decision`) | Keep both; the event carries the action-level value; mapping table in §2.4 |
| C-4 | **Existing `/v1/dashboard/summary`** (gateway, raw tables) overlaps with the new reporting API | Keep it for decisions and audit; the new metrics pages use reporting-api only. Mark summary as legacy in docs |
| C-5 | **Resource budget.** The host has 15.8 GB RAM and 33 GB free on C: (where Docker Desktop data normally lives). Kafka + Spark + Postgres + Ollama + frontend builds are tight | Ollama moves to a profile (`model`), off during the 1M run; Spark driver memory capped; measured in step 12 |
| C-6 | **Late data vs the 1M count.** Events later than the watermark are dropped from windowed aggregates, so "events aggregated = 1,000,000" only holds when the backfill stays inside the watermark | Backfill produces bounded disorder (less than the watermark). Late-event behaviour is tested separately and counted (DLQ / `late_events_total`) |
| C-7 | **P99 latency in a streaming aggregation**: `percentile_approx` support in streaming stateful aggregation must be confirmed on the pinned Spark version | Spike in step 6. Fallback: fixed latency histogram buckets aggregated in-stream, P99 derived at read time |
| C-8 | **Previous audit advice** said Kafka/Spark are not mandatory | The brief now requires them, which is legitimate. The cost is complexity and memory, recorded in the risk register |

## 7. Security risks introduced by the refactor

Full register in `risks-and-mitigations.md`. Headline items:
- Kafka has no authentication and is unencrypted by default.
- New services widen the attack surface.
- The `metadata` and `caller_id` fields could leak personal data into analytics.
- The processor needs DB write access.
- The Spark image is large, with many transitive JVM dependencies, which affects Trivy findings.
- The DLQ holds malformed payloads that may contain unexpected content.
- Aggregates can enable inference attacks on small tenants.
