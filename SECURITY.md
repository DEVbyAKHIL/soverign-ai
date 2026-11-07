# Security

## Reporting
Report vulnerabilities privately to the project maintainers; do not open public issues.

## Controls in place (Phase 1)
- JWT: algorithm pinned, issuer/audience/expiry required, 15-minute default TTL, role-clamped scopes.
- RBAC scopes: `ai:invoke`, `policy:read`, `policy:write`, `audit:read`, `review:decide`, `agent:manage`.
- Tenant isolation through `TenantScope`; cross-tenant lookups return 404.
- Audit table is append-only at the database level.
- Strict CORS (no wildcard), security headers, HSTS outside development, request-size limit.
- Secrets only via environment; `.env` is git-ignored; config refuses placeholder secrets.
- Containers: non-root, `no-new-privileges`, dropped capabilities, read-only root filesystem, ports bound to 127.0.0.1.
- CI: Ruff, Pytest (80% coverage gate), Bandit, Semgrep, pip-audit, Trivy (fs + image), CycloneDX SBOM.

## AI-specific controls (Phases 3–4)
- Prompts, attachments, retrieved documents and embedding inputs are treated as untrusted. Injection
  in content the caller *passes along* (indirect) is quarantined even when the caller's own prompt is
  benign.
- The optional LLM reviewer must be self-hosted. It receives untrusted content as delimited data, its
  answer is restricted to a fixed label enum, and it can never downgrade a strong rule match.
- ML models are plain JSON artifacts (no pickle). spaCy's model is pinned by SHA-256.
- Source files are checked for invisible/bidirectional control characters ("Trojan Source") by
  Bandit B613 and `tests/security/test_source_hygiene.py`.
- The production image contains no `pip`.

## Agent controls (Phase 5)
- Agents must be registered per tenant; unknown or foreign agents are refused (`403`, no existence oracle).
- An agent token can only act as its own agent. Allow-lists for models and tools; an empty tool list means no tools.
- Per-agent rate limit (fails closed if Redis is down) on top of the per-caller limit.
- Behavioural anomalies hold requests for review (≥ 0.70) or quarantine them (≥ 0.90), with named reasons.
- `tests/unit/test_docker_image.py` fails if the production image would miss a first-party package
  or if runtime code imports training code.

## Production
Terminate TLS upstream; use managed Postgres/Redis; load secrets from a secret manager (details under "Secrets" below); rotate DB, Redis, JWT and HMAC keys; prefer an external OIDC provider with asymmetric keys. Transport and at-rest encryption requirements: see the end of this file.

## Streaming and hardening (phases S1-S3 and post-implementation)

### Database accounts (least privilege)

Each service has its own login; only `migrate` (and the test suite) uses the database owner.

| Role | Used by | Privileges | Created in |
|---|---|---|---|
| owner (`POSTGRES_USER`) | `migrate` only | DDL, all data | image init |
| `gateway_app` | gateway | SELECT app tables; INSERT decisions, detections, audit events, agent events, outbox, agents; UPDATE decisions, agents. **No DELETE, no aggregates** | migration 0009 |
| `outbox_publisher` | event-publisher | SELECT, UPDATE on `event_outbox` only | 0009 |
| `stream_processor` | Spark processor | SELECT, INSERT, UPDATE on `aggregated_*`, `pipeline_*` | 0007 |
| `reporting_ro` | reporting-api | SELECT on `aggregated_*`, `pipeline_progress` | 0008 |
| `retention_job` | retention job | SELECT, DELETE on retained tables; anchors; run log. **Never UPDATE audit** | 0009 |

`tests/integration/test_db_roles.py` asserts these grants. `audit_events` stays append-only: UPDATE is always rejected by a trigger, and DELETE only succeeds inside a retention transaction that first records a chain anchor.

### Secrets

| Topic | Practice |
|---|---|
| Source | Environment variables only. `.env` is git-ignored |
| Creating `.env` | `python scripts/init_env.py` generates it from `.env.example`. Secrets are never printed |
| Checking `.env` | `--check` validates presence and placeholders; `--add-missing` adds new ones |
| Validation at start | Gateway, reporting-api, publisher, Spark processor and retention job all validate their configuration at start and refuse missing values, `CHANGE_ME` placeholders, short keys, and reuse of the same value for `JWT_SECRET`, `ENTITY_HASH_KEY` and `FINGERPRINT_HMAC_KEY` |
| Compose | Fails before starting if a required secret is unset (`${VAR:?}`) |
| Per-service scope | Services receive only the secrets they use: the publisher gets only its DB login; the producer gets none; the reporting-api gets JWT verification settings and its read-only login |
| Committed secrets | CI's Semgrep `p/secrets` and Trivy `secret` scanners reject them |
| Production | Do not use a `.env` file. Inject the same variables from a secret manager: HashiCorp Vault, AWS Secrets Manager / Parameter Store, Azure Key Vault, GCP Secret Manager, or Kubernetes Secrets with encryption at rest (optionally External Secrets Operator or the Secrets Store CSI driver). Rotate DB passwords (re-run `migrate`), `JWT_SECRET` (tokens expire in ≤ 1 h) and HMAC keys (see below) |

### Keyed fingerprints (HMAC)

- Prompt content and model output are fingerprinted with HMAC-SHA256 under `FINGERPRINT_HMAC_KEY`, a separate secret, with purpose-specific subkeys (`gateway/core/fingerprint.py`). They are stored as `content_hmac` and `output_hmac`.
- Plain SHA-256 was removed, because a guessable prompt could otherwise be confirmed from its hash.
- The key is never logged or returned. Rotating it changes future fingerprints only, so repeat detection restarts for at most the 10-minute feature window.
- Entity values use the separate `ENTITY_HASH_KEY` (keyed entity hashes and pseudonyms), and `caller_id` in events is an HMAC pseudonym under a subkey of it.
- The audit hash chain still uses SHA-256: it protects integrity and is not a lookup key for sensitive values.

### Data retention

`ops/retention.py`, run with `docker compose --profile ops run --rm retention`, deletes data older than its configured period:

| Data | Variable | Default |
|---|---|---|
| Decisions | `RETENTION_DECISIONS_DAYS` | 365 days |
| Detections | `RETENTION_DETECTIONS_DAYS` | 365 days |
| Audit events | `AUDIT_RETENTION_DAYS` | 365 days |
| Agent events | `RETENTION_AGENT_EVENTS_DAYS` | 30 days (minimum 8) |
| Aggregates | `RETENTION_AGGREGATES_DAYS` | 90 days |
| Pipeline counters | `RETENTION_PIPELINE_DAYS` | 30 days |
| Published outbox rows | `RETENTION_OUTBOX_DAYS` | 7 days |
| Kafka topics | `KAFKA_*_RETENTION_HOURS` | Applied by `kafka-init` |

How the job stays safe and auditable:
- Children are deleted before parents, so no foreign key is violated: a decision is removed only when no detection or audit event references it.
- Audit events are pruned per tenant as a chain prefix, after recording an anchor hash, so `/v1/audit/verify` still passes.
- An optional gzip JSONL archive (`--archive-dir`) can be written first.
- Every run is logged in `retention_runs`.
- It runs as `retention_job`.
- `--dry-run` only counts.

Procedure: `docs/runbook.md`.

### Scanning (CI fails on any fixable CRITICAL/HIGH finding)

| Scanner | Scope |
|---|---|
| Bandit | Every Python package (gateway, policy, audit, detection, agents, events, publisher, producer, streaming, reporting, ops, scripts) |
| Semgrep | Whole repository: `p/python`, `p/secrets`, `p/dockerfile` |
| pip-audit | Every service's requirements: `requirements.txt`, `-stream`, `-reporting`, `-spark`, `-dev` |
| npm audit | Frontend (`--audit-level=high`) |
| Trivy image | Every custom image: gateway, streaming, reporting, spark, frontend (`ignore-unfixed`, CRITICAL+HIGH, Trivy 0.76.1). Every image blocks on OS packages; gateway, streaming, reporting and frontend also block on application libraries. Each image's final stage applies OS security updates at build time. `.trivyignore` holds reviewed exceptions only, and is empty |

**Documented exception: Java libraries bundled inside Apache Spark.** Measured on 2026-11-07 for Spark 3.5.8: 83 fixable HIGH/CRITICAL findings (4 critical). They are in libraries the Spark distribution itself ships: jackson-databind/core, protobuf-java, netty, guava, libthrift and others, mostly from the Hadoop client stack. Spark 3.5.9 has 81, so a patch upgrade doesn't fix them. Replacing individual jars inside Spark risks breaking it, so these are reported (a CI artifact), not blocking.

Mitigations in this deployment:
- Spark runs in local mode with the UI disabled.
- The container publishes no ports and is reachable only on the internal Compose network.
- It runs as non-root with a read-only root filesystem and no Linux capabilities.
- It reads only schema-validated JSON events from Kafka.
- It reaches only Kafka and Postgres.

Remediation path: move to Spark 4.x, which ships newer dependencies, and re-baseline.
| Trivy fs | Dependencies, secrets and IaC misconfiguration, plus a CycloneDX SBOM |

### Transport and storage encryption (production requirements)

The local Compose stack does **not** encrypt traffic between containers and does not encrypt volumes; all of it runs on one host's private Docker network, with host ports bound to 127.0.0.1. Before any multi-host or production deployment:

| Area | Requirement |
|---|---|
| Ingress | TLS 1.2+ (reverse proxy or ingress) for the gateway, reporting-api and dashboard. HSTS is already sent outside development |
| Kafka | `SASL_SSL` listeners (SCRAM or mTLS) with ACLs: gateway outbox publisher and producer write `raw`; Spark reads `raw`, writes `valid` and `dead-letter` |
| PostgreSQL | `ssl = on`, with clients using `sslmode=verify-full` (add `?ssl=verify-full` to the asyncpg URLs and `sslmode=verify-full` to `PROCESSOR_DSN`) |
| Redis | TLS (`--tls-port`) or a managed TLS endpoint |
| At rest | Encrypted volumes or disks for Postgres, Kafka and Spark checkpoints, or managed services with encryption at rest; encrypted backups |
