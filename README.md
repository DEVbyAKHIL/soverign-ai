# Sovereign AI Access Gateway: A Real-Time Streaming Data Backend for AI Governance, Security, and Operational Analytics

A policy-enforcement and audit gateway for AI workloads, extended into a streaming data backend: every policy decision becomes a governance event that flows through Kafka to stream processing, an aggregate store and a live reporting API (streaming phases below).

The gateway sits between users, AI agents, applications and model endpoints, and decides — with explainable, tamper-evident evidence — whether a given caller may send given data to a given model.

> The platform provides technical controls and audit evidence that support AI governance, traceability, data protection, and security requirements. It does not replace legal advice, DPO review, formal certification, enterprise risk management, or regulatory approval.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Foundation: FastAPI, Postgres, Redis, JWT, tenancy, health, logging, Docker, CI, React skeleton | **Done** |
| 2 | Policy-as-code engine, decision service, hash-chained audit trail, decision/audit/policy/review APIs | **Done** |
| 3 | Layered PII / sensitive-data detection (regex+validators, Presidio NER, sensitivity classifier), redaction, masking, pseudonymisation, keyed hashing, evaluation | **Done** |
| 4 | Prompt-injection and intent-risk detection (rules, char-n-gram classifier, optional local LLM reviewer), direct vs indirect injection, `/v1/rag/query` | **Done** |
| 5 | Agent registry and allow-lists, behaviour events, rolling features, Isolation Forest + statistical anomaly detection, `/v1/agents` and risk API | **Done** |
| 6 | Forwarding to a self-hosted model (Ollama), output guardrails (PII redaction; secret, system-prompt and attack-payload blocking), `model.response` audit events; injection model retrained with a real public dataset | **Done** |
| 7 | Dashboard: overview KPIs and trends, playground, decisions & review queue, policies, agents, audit trail with chain verification | **Done** |
| 8 | Hardening | Planned |

### Streaming data backend (Task 2)

| Phase | Scope | State |
|---|---|---|
| S1 | Kafka (KRaft), versioned event schema, transactional outbox + separate `event-publisher`, `event-producer` (continuous + backfill, failure injection), 1M-event ingestion verification | **Implemented** (see [Streaming quick start](#streaming-quick-start)) |
| S2 | Spark Structured Streaming: validation, dead-letter routing, 11 windowed aggregations (8 queries), idempotent PostgreSQL upserts, checkpoints, restart recovery | **Implemented** (`docs/spark-streaming.md`, `docs/aggregations.md`, `DATA_FLOW.md`) |
| S3 | Read-only `reporting-api` (12 endpoints, tenant from JWT, keyset pagination, small-cell suppression) and 7 live dashboard pages polling every 5 s | **Implemented** (`docs/api.md`) |
| S4 | Model-registry trust fix (model, jurisdiction, purposes from `ai_assets`) | Planned |

Plan and design: `docs/refactor-plan.md`, `docs/architecture-after-refactor.md`, `docs/implementation-order.md`.

## Quick start from a fresh clone (Docker)

Prerequisites:

| Prerequisite | Requirement |
|---|---|
| Docker | Docker Engine or Desktop with Compose v2 |
| Python | 3.10+ on the host, standard library only (no virtualenv needed for these steps) |
| RAM for Docker | About 8 GB |
| Disk | About 15 GB for images and volumes |

```bash
git clone https://github.com/DEVbyAKHIL/soverign-ai.git && cd soverign-ai
python scripts/init_env.py                 # writes .env with generated secrets (never printed)
docker compose up --build -d               # build, migrate (automatic), start everything
docker compose ps                          # wait until every service is "healthy" (2-5 min)
curl http://127.0.0.1:8000/readyz          # {"status":"ready"}
```

The steps above do the following automatically:
- The `migrate` service runs all Alembic migrations, creates the per-service database logins and syncs the policies before any service starts.
- Postgres creates the disposable `gateway_test` database on first start.

Run the tests and the end-to-end verification:

```bash
docker compose --profile test run --rm --build tests          # gateway, outbox, Kafka, reporting
docker compose --profile test run --rm --build spark-tests    # Spark window maths
python scripts/verify_pipeline.py --total 1000000             # 1M events, processor kill + restart
```

Stop with `docker compose down` (keeps data); add `-v` to delete all volumes.

After pulling a newer version, add new secrets to your existing `.env` with `python scripts/init_env.py --add-missing`. Check `.env` at any time with `python scripts/init_env.py --check`.

Pull the local models once (about 2.3 GB; requests are never sent off the host):

```bash
docker compose exec ollama ollama pull llama3.2:3b
docker compose exec ollama ollama pull nomic-embed-text
```

Dashboard: http://localhost:5173 (sign in with a development role, e.g. `admin`, and try the Playground) · API docs (development only): http://localhost:8000/docs

## Streaming quick start

`docker compose up --build -d` also starts the streaming services:

| Service | What it does |
|---|---|
| `kafka` | Event backbone, KRaft mode (no ZooKeeper), internal network only |
| `kafka-init` | One-shot: creates `governance.events.raw` (6 partitions), `.valid` (6), `.dead-letter` (1) |
| `event-publisher` | Relays the gateway's transactional outbox to Kafka; marks rows published only after Kafka acknowledges |
| `event-producer` | Synthetic governance telemetry, continuous mode at `PRODUCER_RATE` events/s (default 10) |
| `reporting-api` | Read-only metrics API over the aggregate tables (`reporting_ro` login), http://127.0.0.1:8001; dashboard pages under **Real-time (streaming)** |
| `spark-stream-processor` | Spark Structured Streaming: validates `raw` → `valid` / `dead-letter`, 8 windowed aggregation queries → `aggregated_*` tables (upserts), checkpoints on a volume |

Generate and verify one million timestamped events:

```bash
make verify-1m            # or: docker compose run --rm --build event-producer python -m producer verify-1m --total 1000000
```

The command backfills the events under a fresh run id and reads every one back from Kafka. It
checks each event against `schemas/governance_event/1.0.0.json`, checks its UTC timestamp, counts
distinct `event_id`s and prints a PASS/FAIL summary (exit code 1 on any mismatch).

End-to-end check through Spark, with the processor killed and restarted mid-run (needs the stack up):

```bash
make verify-pipeline      # or: python scripts/verify_pipeline.py --total 1000000
```

It prints events produced / consumed / aggregated / dead-lettered and `Processor restart recovery: PASSED|FAILED`.

Every service connects to PostgreSQL with its own least-privilege login: `gateway_app`, `outbox_publisher`, `stream_processor`, `reporting_ro` and `retention_job`. The `migrate` service creates them from the `*_DB_PASSWORD` variables, which `init_env.py` generates. Data retention: `docker compose --profile ops run --rm retention --dry-run`. Scaling experiment: `python scripts/scaling_experiment.py`.

Optional tools (not started by `up`): `make kafka-ui` → http://127.0.0.1:8080 (read-only), `make monitoring` → Prometheus on http://127.0.0.1:9090.

More: `docs/kafka.md`, `docs/docker-compose.md`, `docs/runbook.md`.

## Local development (no Docker for the app)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

Tests use an in-memory Redis fake. Database tests (decision flows, audit chain, policy versioning) run
when `TEST_DATABASE_URL` points to a disposable database whose name ends in `_test`; otherwise they're skipped.
With the Docker stack running:

```bash
docker compose exec postgres sh -c 'createdb -U "$POSTGRES_USER" gateway_test'
export TEST_DATABASE_URL="postgresql+asyncpg://gateway:<POSTGRES_PASSWORD>@127.0.0.1:5432/gateway_test"
pytest
```

Use `127.0.0.1`, not `localhost`. On Windows, `localhost` tries IPv6 first and adds about 2 s per connection.

### Full suite on Linux (includes NER)

spaCy's compiled libraries can be blocked by Windows Smart App Control, so on such hosts the NER tests
are skipped (`NER_MODE=optional` in tests). The Docker test runner always runs them with NER required:

```bash
make test-docker      # full suite on Linux against the gateway_test database
make evaluate         # PII, injection and agent-anomaly metrics (see EVALUATION.md)
```

## Policies

Policies are YAML files in `policy/policies/` (see `ARCHITECTURE.md` for the semantics and
`docs/api.md` for the API). To change a policy, edit it, **bump its `version`**, and redeploy. The
gateway refuses to start if any policy is invalid, and `policy.sync` refuses changed content
under an existing version.

## Getting a development token

`POST /v1/auth/dev-token` is only enabled when `ENVIRONMENT` is `development` or `test`. In production, tokens come from your OIDC provider.

```bash
curl -s -X POST localhost:8000/v1/auth/dev-token -H 'Content-Type: application/json' \
  -d '{"subject":"00000000-0000-4000-8000-000000000001","tenant_id":"00000000-0000-4000-8000-0000000000aa","role":"admin"}'
```

Roles → scopes are defined in `gateway/core/security.py`.

## Layout

See `ARCHITECTURE.md`. Implemented: `gateway/` (API), `policy/` (policy engine), `audit/` (hash-chained audit),
`detection/` (PII and prompt-injection detection), `agents/` (agent registry and behaviour anomalies) and
`ml/` (synthetic data, training, evaluation). `detection/output` holds the output guardrails.
Streaming: `events/` (event contract helpers), `schemas/governance_event/` (versioned JSON Schema),
`publisher/` (outbox relay), `producer/` (synthetic telemetry and verification), `streaming/` (Spark processor),
`scripts/verify_pipeline.py` (end-to-end verification). Data flow: `DATA_FLOW.md`.
Further reading: `EVALUATION.md`, `docs/model-cards.md`, `docs/privacy.md`.
