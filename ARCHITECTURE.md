# Architecture

## Components

```
browser ──► frontend (nginx, :5173) ──/api──► gateway (FastAPI, :8000) ──► PostgreSQL 16 (+pgvector)
                                                     └──────────────────► Redis 7 (rate limits)
migrate (one-shot): alembic upgrade head && python -m policy.sync
```

| Package | Responsibility |
|---|---|
| `gateway/` | Stateless FastAPI service: auth, tenancy, rate limits, routers, the decision service. Scales horizontally. |
| `policy/` | Policy schema and safety validation (`validator.py`), loading and versioning (`loader.py`), deterministic evaluation (`engine.py`), DB version sync (`sync.py`). |
| `audit/` | Per-tenant SHA-256 hash chain (`hash_chain.py`), append and verify (`service.py`). |
| `migrations/` | Alembic. `audit_events` is append-only via a Postgres trigger. |
| `detection/`, `agents/`, `ml/` | Phases 3–6 (empty for now). |

## Request lifecycle (`POST /v1/ai/infer`)

1. **Middleware**: validates `X-Request-ID` and rejects bodies over `MAX_REQUEST_BYTES`.
2. **AuthN/AuthZ**: the JWT becomes a `Principal`; the request needs the `ai:invoke` scope; Redis rate limit per tenant and caller.
3. **Identity binding**: the body's `tenant_id`, `caller_id` and `caller_type` must match the token. A caller can't act as another caller or tenant.
4. **Context**: builds an `EvaluationContext` (classification, jurisdiction, purpose, model and so on). Unlabelled data is treated as `confidential`. *(Phases 3–5 add detected entities, intent label, risk and anomaly scores here.)*
5. **Policy evaluation**: pure and deterministic (see below).
6. **Persist**: in one DB transaction, insert the `decisions` row and append a `decision.created` audit event to the tenant's hash chain. Neither is stored without the other.
7. **Side effects**: `alert` emits a structured warning log and increments `gateway_policy_alerts_total`. `create_review_case` / `require_human_review` set `review_status = pending`.
8. **Response**: decision, explanation, matched policies, audit event ID.

The raw prompt is **never stored**. The audit event records a SHA-256 fingerprint, character count and part count.

## Policy engine

* Policies live in `policy/policies/*.yaml` (Git is the source of truth). They load once at start-up; **any invalid file stops the gateway from starting** (fail closed).
* `match` keys are ANDed. List keys match by membership. `data_sensitivity` / `entity_types` match on any overlap. `min_risk_score` / `min_agent_anomaly_score` are thresholds. `stage` defaults to `request`.
* All matching policies contribute actions. The decisive action follows a fixed precedence:
  `deny > quarantine > require_human_review > route_to_approved_model > pseudonymise > redact > mask > allow`.
  An explicit `allow` therefore **cannot override** a `deny`.
* If no policy matches: requests to an `unknown` jurisdiction or carrying `high_risk` data are **denied by default**; everything else is allowed.
* Policies are ordered by (severity, policy_id). The decisive policy is the first in that order that carries the decisive action, so results are reproducible.
* **Versioning**: each policy has a semver `version`. The policy set version is a hash over every `policy_id@version` plus its YAML hash, and it's stored on every decision. `policy.sync` records each version in the `policies` table and refuses to accept changed content under an existing version.

## Audit trail

* `event_hash = SHA-256(previous_hash + canonical_json(event))`, where canonical JSON has sorted keys and no whitespace. The first event's `previous_hash` is 64 zeros.
* There is one chain per tenant, ordered by `seq`. A transaction-level advisory lock serialises appends per tenant, so concurrent writers can't fork the chain.
* `created_at` is set by the application, so the hashed value is exactly what's stored.
* A database trigger rejects `UPDATE`/`DELETE` on `audit_events`. `GET /v1/audit/verify` recomputes the chain and reports the first broken link. This catches tampering even by someone who bypasses the trigger (a test covers that case).

## Data model additions (migration 0002)

* `audit_events.tenant_id`, `audit_events.seq` (identity), unique `(tenant_id, seq)`.
* `decisions.policy_set_version`, `decisions.review_status`, unique `(tenant_id, request_id)` for idempotency.
