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
| GET | `/v1/agents/{agent_id}/risk` | `agent:manage` | 5 (not yet) |
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
  "model_response": null
}
```

Status codes: `403 identity_mismatch` if the body claims a different tenant, caller or caller type
than the token; `409 duplicate_request` if the tenant has already used the `request_id`; `422` for
invalid input. Submitted values are never echoed in error messages; `429` means the rate limit was
hit.

The gateway does not forward requests to a model yet. It returns only the decision, so `model_response`
is always `null`. When policies redact, mask or pseudonymise, `transformed_content` holds the content
exactly as it may be forwarded. `part` is 0 for the prompt (or first embedding input) and 1.. for
attachments (or further inputs).

## POST /v1/rag/query

Same envelope as `/v1/ai/infer`, with a query and the retrieved documents in place of `prompt`:

```json
{
  "...": "request_id, tenant_id, caller_*, requested_*, destination_jurisdiction, declared_purpose",
  "query": "What is the refund policy?",
  "documents": [
    {"doc_id": "kb-1", "source": "kb/refunds.md", "content": "…", "classification": "internal"}
  ]
}
```

* The query is direct input; every document is indirect, untrusted input.
* A document's `classification` sets a floor for the request's classification.
* If any document carries an injection, the request is quarantined (`action_taken: "quarantine"`,
  `review_status: "pending"`). `content_verdicts` shows which `doc_id` was responsible.
* When transformed, `transformed_content` is `{"query": "...", "documents": [{"doc_id", "content"}]}`.
* Limits: query ≤ 32,000 characters, ≤ 50 documents, each ≤ 20,000 characters.

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
