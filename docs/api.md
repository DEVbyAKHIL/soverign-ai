# API reference

All endpoints except `/healthz`, `/readyz`, `/metrics` and the development token issuer require
`Authorization: Bearer <JWT>`. Errors use one envelope:

```json
{"error": {"code": "forbidden", "message": "Missing required scope(s): audit:read", "request_id": "…"}}
```

| Method | Path | Scope | Phase |
|---|---|---|---|
| POST | `/v1/ai/infer` | `ai:invoke` | 2 |
| POST | `/v1/ai/embed` | `ai:invoke` | 2 |
| POST | `/v1/rag/query` | `ai:invoke` | 4 |
| GET | `/v1/decisions/{decision_id}` | `audit:read` | 2 |
| GET | `/v1/audit/events` | `audit:read` | 2 |
| GET | `/v1/audit/verify` | `audit:read` | 2 |
| POST | `/v1/agents` | `agent:manage` | 5 |
| GET | `/v1/agents` | `audit:read` | 5 |
| GET | `/v1/agents/{agent_id}` | `audit:read` | 5 |
| GET | `/v1/agents/{agent_id}/risk` | `audit:read` | 5 |
| GET | `/v1/policies` | `policy:read` | 2 |
| POST | `/v1/policies/validate` | `policy:write` | 2 |
| POST | `/v1/reviews/{decision_id}/decision` | `review:decide` | 2 |
| POST | `/v1/auth/dev-token` | none, development/test only | 1 |
| GET | `/v1/auth/me` | any valid token | 1 |
| GET | `/healthz`, `/readyz`, `/metrics` | none | 1 |

## POST /v1/ai/infer

```json
{
  "request_id": "uuid",
  "tenant_id": "uuid",            // must equal the token's tenant
  "caller_type": "user",          // must equal the token's caller type
  "caller_id": "uuid",            // must equal the token's subject
  "agent_id": null,               // required when caller_type is "agent"
  "requested_model": "gpt-4o",
  "requested_endpoint": "https://api.example.com/v1/chat",
  "destination_jurisdiction": "eu | non_eu | unknown",
  "declared_purpose": "customer_support_summarisation",
  "data_classification": "public | internal | confidential | restricted | high_risk",  // default: confidential
  "prompt": "…",                   // ≤ 32,000 chars
  "attached_content": [],          // ≤ 20 items
  "tool_calls": [{"name": "search_kb", "arguments": {}}],   // ≤ 20; checked against the agent's allow-list
  "metadata": {}
}
```

`/v1/ai/embed` takes the same envelope with `"input": ["…"]` in place of `prompt`/`attached_content`.

Response:

```json
{
  "decision_id": "uuid",
  "request_id": "uuid",
  "final_decision": "allow | allow_with_modification | review | deny",
  "action_taken": "deny",
  "risk_score": 0.0,
  "explanation": "Blocked by policy block_pii_to_non_eu_models@1.2.0 (…). Conditions met: …",
  "matched_policies": ["block_pii_to_non_eu_models@1.2.0"],
  "side_effects": ["alert", "create_review_case"],
  "transforms": [],
  "route_to": null,
  "review_status": "not_required | pending",
  "policy_set_version": "caf25e67fd43f2dd",
  "data_classification": "confidential",   // effective, after detection
  "intent": {"label": "prompt_injection", "confidence": 0.9, "risk_score": 0.93,
             "signals": ["instruction_override"]},   // the caller's own input
  "content_verdicts": [                    // one per part; doc_id set for RAG documents
    {"part": 0, "source": "direct", "label": "prompt_injection", "risk_score": 0.93,
     "signals": ["instruction_override"], "doc_id": null}
  ],
  "detections": [
    {"entity_type": "EMAIL", "confidence": 0.99, "detector": "regex", "model_version": "regex-1.0.0+terms-2",
     "start_offset": 45, "end_offset": 61, "part": 0, "risk_level": "medium"}
  ],
  "transformed_content": null,             // {"prompt": "...", "attached_content": [...]} when transformed
  "audit_event_id": "uuid",
  "model_response": {                      // forwarding outcome (Phase 6)
    "status": "ok",                        // ok | blocked | error | not_forwarded
    "model": "llama3.2:3b", "latency_ms": 840.2,
    "text": "...the guarded answer...",    // null when blocked
    "usage": {"prompt_tokens": 41, "completion_tokens": 120},
    "output_guard": {"action": "redacted", "reasons": ["redacted:1"], "entity_types": ["EMAIL"],
                     "intent": {"label": "benign"}}
  }
}
```

Status codes: `403 identity_mismatch` if the body claims a different tenant, caller or caller type
than the token; `409 duplicate_request` if the tenant has already used the `request_id`; `422` for
invalid input. Submitted values are never echoed in error messages; `429` means the rate limit was
hit.

Approved requests (`allow` / `allow_with_modification`, no pending review) are forwarded to the
self-hosted model backend in their transformed form, and the answer passes the output guard.
Otherwise `model_response` is `{"status": "not_forwarded", "reason": "not_approved" |
"pending_review" | "backend_disabled"}`. `/v1/ai/embed` returns `"embeddings"` instead of `"text"`.
When policies redact, mask or pseudonymise, `transformed_content` holds the content
exactly as it may be forwarded. `part` is 0 for the prompt (or first embedding input) and 1.. for
attachments (or further inputs).

## POST /v1/rag/query

Same envelope as `/v1/ai/infer`, with a query and the retrieved documents in place of `prompt`:

```json
{
  "...": "request_id, tenant_id, caller_*, requested_*, destination_jurisdiction, declared_purpose",
  "query": "What is the refund policy?",
  "documents": [
    {"doc_id": "kb-1", "source": "kb/refunds.md", "content": "…", "classification": "internal",
     "dataset": "kb"}   // optional; defaults to the URL host or the path without the file name
  ]
}
```

* The query is direct input; every document is indirect, untrusted input.
* A document's `classification` sets a floor for the request's classification.
* If any document carries an injection, the request is quarantined (`action_taken: "quarantine"`,
  `review_status: "pending"`). `content_verdicts` shows which `doc_id` was responsible.
* When transformed, `transformed_content` is `{"query": "...", "documents": [{"doc_id", "content"}]}`.
* Limits: query ≤ 32,000 characters, ≤ 50 documents, each ≤ 20,000 characters.

## Agents

`POST /v1/agents` (scope `agent:manage`):

```json
{"name": "support-bot", "agent_type": "support", "allowed_models": ["gpt-4o"],
 "allowed_tools": ["search_kb"], "max_requests_per_minute": 60}
```

The agent authenticates with a token whose subject is its `id` and whose caller type is `agent`. Its
requests must set `caller_type: "agent"`, `caller_id` and `agent_id` to that id. Every response then
includes the behaviour verdict:

```json
"agent": {"agent_id": "…", "anomaly_score": 0.87, "is_anomalous": true,
          "reasons": ["new_external_destination", "high_sensitive_field_access"],
          "method": "isolation_forest+stats", "model_version": "agent-iforest-1.0.0+agent-stats-1.1.0",
          "features": {"request_rate_log_ratio": 0.1, "…": 0}, "baseline_events": 812,
          "requests_per_minute": 0.6}
```

`GET /v1/agents/{id}/risk` returns the agent, its long-running `risk_score`, a fresh verdict over
the last 10 minutes (`current`), and 7-day totals (`events_7d`, `denied_7d`,
`sensitive_fields_7d`, `last_seen`). `GET /v1/agents?min_risk=0.5` lists agents riskiest first.
Errors: `403 unknown_agent` (not registered for this tenant), `403 identity_mismatch` (an agent
acting as another agent), `429` (the agent's own rate limit).

## POST /v1/reviews/{decision_id}/decision

```json
{"outcome": "approve | reject", "reason": "≥ 3 chars"}
```

Only decisions with `review_status = pending` can be reviewed (otherwise `409`). Reviewers cannot
decide on their own requests (`403 separation_of_duties`). Each review appends a `review.decided`
audit event.

## GET /v1/audit/events

Query: `decision_id`, `event_type` (e.g. `decision.created`), `before_seq` (pagination cursor),
`limit` (1–200, default 50). Newest first, scoped to the caller's tenant.

## GET /v1/audit/verify

Recomputes the caller's tenant hash chain:

```json
{"valid": true, "events_checked": 56, "first_invalid_seq": null, "reason": null}
```

## POST /v1/policies/validate

```json
{"policy_yaml": "policy_id: …"}
```

Returns `{valid, errors[], policy_id, version, version_bump_required}`. Validation does not deploy
the policy. Policies are deployed by committing them to `policy/policies/` and redeploying.

## GET /v1/dashboard/summary?days=7

Requires `audit:read`. Tenant-scoped aggregates for the dashboard: `total`, `by_decision`,
`pending_reviews`, `avg_risk`, `timeline` (per day: counts per final decision and average risk),
`top_policies`, `entity_types`, `intents`, `model_responses`, `output_guard`, `agents` (riskiest 8).
`days` is 1-90.

## GET /v1/decisions?review_status=pending&final_decision=deny&limit=50

Requires `audit:read`. The tenant's most recent decisions, newest first, without content.

## Reporting API (`reporting-api`, phase S3)

A separate read-only service on port 8001 serves the streaming aggregates. The dashboard calls it through nginx at `/reporting/...`, so it is same-origin and nginx allows only GET.

| Property | Behaviour |
|---|---|
| Auth | A gateway-issued bearer JWT carrying the `audit:read` scope (roles `admin`, `security_analyst`). Missing or invalid token: 401; missing scope: 403 |
| Tenant | **Always taken from the token.** There is no tenant parameter |
| Database access | Login `reporting_ro`: `SELECT` on `aggregated_*` and `pipeline_progress` only |
| OpenAPI | `/docs` and `/openapi.json` in development |

**Common query parameters**

| Parameter | Rule |
|---|---|
| `from`, `to` | ISO-8601. Defaults: the last 60 minutes. `from` must be before `to`; range at most 7 days |
| `limit` | 1-1000 (default 500) |
| `cursor` | The `next_cursor` from the previous page (keyset pagination) |
| `metric` | Optional; restricts the response to one metric |

Invalid values return 422 with the gateway's error format.

| Endpoint | Data | Extra filters |
|---|---|---|
| `GET /v1/metrics/overview` | KPIs from each table's most recent windows: requests in the last 5 minutes and per minute, decisions by type, deny rate, average and P99 latency, failed evaluations, PII entities, injection attempts, the 5 riskiest agents, and processor freshness | - |
| `GET /v1/metrics/requests` | 1-minute tumbling `requests`, `tokens` (tenant totals by default) | `agent_id` |
| `GET /v1/metrics/policies` | `count` by decision (1-minute tumbling); `deny_rate`, `failed_evaluations` (5-minute sliding, decision `*`) | `decision` |
| `GET /v1/metrics/latency` | `avg_ms`, `p99_ms`, `max_ms` (5-minute sliding) | - |
| `GET /v1/metrics/pii` | `pii_entities`, `events_with_pii` per destination model (5-minute tumbling) | `destination_model` |
| `GET /v1/metrics/injections` | `attempts`, `avg_score`, `max_score` per agent (5-minute sliding) | `agent_id` |
| `GET /v1/metrics/agents` | Each agent's latest 10-minute risk window, riskiest first | - |
| `GET /v1/metrics/sovereignty` | `events`, `tokens`, `pii_entities` by jurisdiction x sensitivity (10-minute tumbling) | `destination_jurisdiction`, `sensitivity_level` |
| `GET /v1/agents/{agent_id}/risk` | One agent's risk history. 404 if the agent has no data in the caller's tenant | - |
| `GET /healthz`, `/readyz`, `/metrics` | Liveness, database readiness, Prometheus metrics | - |

**Series response**

```json
{
  "tenant_id": "...", "from": "...", "to": "...", "as_of": "...", "next_cursor": null,
  "items": [{
    "window_start": "...", "window_end": "...", "window_type": "tumbling",
    "dimensions": {"agent_id": "00000000-0000-0000-0000-000000000000"},
    "metric": "requests", "value": 412.0, "event_count": 412, "suppressed": false
  }]
}
```

**Small-cell suppression.** In the PII and sovereignty series, a cell backed by fewer than 5 events (`SUPPRESS_BELOW`) is returned as `value: null, event_count: null, suppressed: true`. This reduces the chance of re-identifying individual requests.
