# Sovereign AI Access Gateway

A policy-enforcement and audit gateway for AI workloads. It sits between users, AI agents, applications and model endpoints, and decides — with explainable, tamper-evident evidence — whether a given caller may send given data to a given model.

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

## Quick start (Docker)

```bash
cp .env.example .env          # then replace every CHANGE_ME value
docker compose up --build -d
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
```

Pull the local models once (about 2.3 GB; requests are never sent off the host):

```bash
docker compose exec ollama ollama pull llama3.2:3b
docker compose exec ollama ollama pull nomic-embed-text
```

Dashboard: http://localhost:5173 (sign in with a development role, e.g. `admin`, and try the Playground) · API docs (development only): http://localhost:8000/docs

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
Further reading: `EVALUATION.md`, `docs/model-cards.md`, `docs/privacy.md`.
