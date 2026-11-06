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
| `detection/` | Sensitive-data detection (`pii/`), prompt-injection / intent detection (`injection/`), transforms (`transforms.py`), entity catalogue (`types.py`), shared JSON linear-model runtime (`linear_model.py`). `output/` inspects model answers (output guardrails). |
| `ml/` | Synthetic data generator, classifier training, evaluation scripts. |
| `agents/` | Agent registry and allow-lists (`profile.py`), rolling behavioural features (`features.py`), Isolation Forest + statistical anomaly detection (`anomaly_detector.py`), long-running risk score (`risk_score.py`). |

## Request lifecycle (`POST /v1/ai/infer`)

1. **Middleware**: validates `X-Request-ID` and rejects bodies over `MAX_REQUEST_BYTES`.
2. **AuthN/AuthZ**: the JWT becomes a `Principal`; the request needs the `ai:invoke` scope; Redis rate limit per tenant and caller.
3. **Identity binding**: the body's `tenant_id`, `caller_id` and `caller_type` must match the token. A caller can't act as another caller or tenant.
4. **Detection** (in a worker thread): runs the layers below over the prompt and every attachment.
   The result yields the effective classification, `data_sensitivity` categories, `entity_types` and a
   risk score. Unlabelled data starts as `confidential`; detection can raise the classification but never lowers it.
   Intent analysis runs per part in the same worker: the prompt or query is **direct** input;
   attachments, retrieved RAG documents and embedding inputs are **indirect**. The worst direct
   verdict becomes `intent_label`, the worst indirect one `indirect_intent_label`, and the risk score
   combines sensitive-data and injection risk (noisy-OR).
   For requests that involve an agent, the agent must be registered to the tenant. An agent caller
   must be that agent. A per-agent rate limit applies, the model and tools are checked against
   the agent's allow-lists, and the agent's recent behaviour (including this request) is scored for
   anomalies before policy evaluation.
5. **Policy evaluation**: pure and deterministic (see below).
6. **Persist**: in one DB transaction, insert the `decisions` row and append a `decision.created` audit event to the tenant's hash chain. Neither is stored without the other.
7. **Side effects**: `alert` emits a structured warning log and increments `gateway_policy_alerts_total`. `create_review_case` / `require_human_review` set `review_status = pending`.
8. **Response**: decision, explanation, matched policies, audit event ID.

The raw prompt is **never stored**. The audit event records a SHA-256 fingerprint, character count and part count.

## Policy engine

* Policies live in `policy/policies/*.yaml` (Git is the source of truth). They load once at start-up; **any invalid file stops the gateway from starting** (fail closed).
* `match` keys are ANDed. List keys match by membership. `data_sensitivity` / `entity_types` match on any overlap. `min_risk_score` / `min_agent_anomaly_score` are thresholds. `stage` defaults to `request`.
* All matching policies contribute actions. The decisive action follows a fixed precedence:
  `deny > quarantine > require_human_review > route_to_approved_model > redact > pseudonymise > mask > allow`.
  An explicit `allow` therefore **cannot override** a `deny`.
* If no policy matches: requests to an `unknown` jurisdiction or carrying `high_risk` data are **denied by default**; everything else is allowed.
* Policies are ordered by (severity, policy_id). The decisive policy is the first in that order that carries the decisive action, so results are reproducible.
* **Versioning**: each policy has a semver `version`. The policy set version is a hash over every `policy_id@version` plus its YAML hash, and it's stored on every decision. `policy.sync` records each version in the `policies` table and refuses to accept changed content under an existing version.

## Sensitive-data detection

| Layer | Component | Detects |
|---|---|---|
| 1 | Regex + validators (`detection/pii/regex_detector.py`) | EMAIL, PHONE (libphonenumber), CREDIT_CARD (Luhn), IBAN (mod-97), NATIONAL_ID (SSN/NINO/labelled), PASSPORT, API_KEY, PASSWORD, DATE_OF_BIRTH, ADDRESS, FINANCIAL, MEDICAL, CUSTOMER_ID, INTERNAL_PROJECT |
| 2 | Presidio + spaCy `en_core_web_sm` (`ner_detector.py`) | PERSON, LOCATION, ORGANISATION |
| 3 | Sensitivity classifier (`sensitivity_classifier.py`) | document class, used only to *raise* the class at ≥0.80 confidence |

* Overlapping spans: the higher risk weight wins, then confidence, then length.
* Each entity type maps to sensitivity categories (`pii`, `financial`, `health`, `secret`, …), a
  classification floor (e.g. CREDIT_CARD → restricted, MEDICAL → high_risk) and a risk weight.
  Only entities with confidence ≥ 0.70 raise the floor; weaker ones are still reported.
* Transforms: matched policies with `redact` / `mask` / `pseudonymise` contribute
  `(transform, scope)` steps. The scope is the policy's `entity_types`, or all sensitive entities.
  Each entity gets the strongest applicable transform. See `docs/privacy.md`.
* Detected values are stored only as keyed per-tenant hashes. See `docs/privacy.md` and `docs/model-cards.md`.

## Prompt-injection and intent detection

| Layer | Component | Role |
|---|---|---|
| Normalisation | `injection/rules.py` | NFKC, zero-width removal, whitespace collapse, leetspeak folding, base64 payload decoding |
| Rules | `injection/rules.py` | Named signals (`instruction_override`, `guardrail_bypass`, `mode_switch`, `persona_hijack`, `system_prompt_request`, `data_export_request`, `exfil_channel`, `delimiter_injection`, `tool_hijack`, `ai_directed_instruction`, `encoded_payload`, `obfuscation`), each with a label and a weight |
| Classifier | `injection/classifier.py` | char_wb 2–5-gram TF-IDF + logistic regression (JSON, no pickle); long inputs are scored on the first and last 4,000 characters |
| Reviewer (optional) | `injection/llm_reviewer.py` | Self-hosted OpenAI-compatible LLM. Only consulted in the uncertain band, never overrides a strong rule hit, only accepts enum labels; any failure means "suspicious" |

Verdict logic (`injection/detector.py`): a strong rule hit (weight ≥ 0.70) decides the label.
Otherwise the classifier's attack class applies above the source's attack threshold. On **indirect**
content the classifier needs a corroborating rule signal or ≥ 0.80 confidence, because per-document
false positives compound across retrieved documents. Below that, the uncertain band is labelled
`suspicious`. Thresholds were chosen on a validation set that is separate from the reported test set.

Policies:

| Policy | Match | Actions |
|---|---|---|
| `block_system_prompt_extraction` | `intent_label` ∈ attacks | deny, alert |
| `quarantine_indirect_injection` | `indirect_intent_label` ∈ attacks | quarantine, alert, create_review_case |
| `alert_suspicious_intent` / `alert_suspicious_documents` | `suspicious` | allow, alert |

An explicit `allow` (such as allow + alert) never overrides the built-in default deny for `unknown`
jurisdictions or `high_risk` data.

`POST /v1/rag/query` takes the query plus the documents your retriever returned. Each document gets
its own verdict (`content_verdicts[].doc_id`), so callers can see which document was poisoned. The
query and documents also go through PII detection and transforms like any other request.

## Agent behaviour anomaly detection

**Registry and least privilege.** `POST /v1/agents` registers an agent with `allowed_models`
(at least one), `allowed_tools` (empty means *no* tools) and `max_requests_per_minute`. Requests
naming an unregistered agent, or another tenant's agent, get `403 unknown_agent`. An agent token can
act only as itself. Users acting *through* an agent get that agent's allow-lists.

**Events.** Every agent request writes an `agent_events` row in the decision transaction: tokens
(≈ characters / 4), sensitive-field count, destination host, datasets (RAG document sources),
tool-call count, content hash, final decision and latency. Raw content is never stored.

**Features.** The last 10 minutes are compared with the agent's own previous 7 days (at most 5,000 rows):

| Feature | Spec behaviour |
|---|---|
| `request_rate_log_ratio` (+ requests per minute) | requests per minute |
| `volume_log_ratio` | tokens / bytes processed |
| `sensitive_per_request`, `sensitive_access_delta` | sensitive fields detected / access frequency |
| `denied_ratio` | denied requests, error rate |
| `new_datasets` | distinct datasets accessed |
| `new_destinations`, `destination_count_log` | distinct destinations, change in destination pattern |
| `tool_calls_delta` | tool-call frequency |
| `retry_rate` (repeated content hashes) | retry rate |
| `off_hours_ratio` (hours with < 2% of baseline activity) | time-of-day pattern |

The features are log-ratios, deltas and fractions, so one model works for agents whose normal
volumes differ by orders of magnitude.

**Detection.** `StatisticalDetector` applies z-score and percentile rules against the agent's
baseline. Every rule weight is ≥ 0.70, so a single clear reason can flag on its own. The
`IsolationForestScorer` (scikit-learn model exported to JSON) adds a multivariate score. The result
is `max(forest, rules)` with named reasons. With fewer than 30 baseline events or 3 active windows,
only absolute rules apply (`insufficient_baseline`): rate limit, denials, retries and sensitive
volume.

**Policies.** `review_anomalous_agent` (score ≥ 0.70 → human review),
`quarantine_severe_agent_anomaly` (≥ 0.90 → quarantine), and `block_unapproved_agent_model` /
`block_unapproved_agent_tool` (→ deny). `agents.risk_score` is an exponentially weighted moving average (α 0.2) of
anomaly scores, used for the riskiest-first `GET /v1/agents` listing.

## Audit trail

* `event_hash = SHA-256(previous_hash + canonical_json(event))`, where canonical JSON has sorted keys and no whitespace. The first event's `previous_hash` is 64 zeros.
* There is one chain per tenant, ordered by `seq`. A transaction-level advisory lock serialises appends per tenant, so concurrent writers can't fork the chain.
* `created_at` is set by the application, so the hashed value is exactly what's stored.
* A database trigger rejects `UPDATE`/`DELETE` on `audit_events`. `GET /v1/audit/verify` recomputes the chain and reports the first broken link. This catches tampering even by someone who bypasses the trigger (a test covers that case).

## Data model additions (migration 0002)

* `audit_events.tenant_id`, `audit_events.seq` (identity), unique `(tenant_id, seq)`.
* `decisions.policy_set_version`, `decisions.review_status`, unique `(tenant_id, request_id)` for idempotency.
* Migration 0003: `detections.part`, `detections.risk_level`.
* Migration 0004: `decisions.intent_label`, `decisions.indirect_intent_label` (indexed by tenant).
* Migration 0005: `agent_events.tenant_id`, `decision_id`, `datasets`, `tool_calls`, `content_hash`;
  index on `(agent_id, created_at)`.

## Model forwarding and output guardrails (Phase 6)

After the decision is committed, `gateway/services/forwarding.py` forwards it to the self-hosted,
OpenAI-compatible backend (`MODEL_BACKEND_URL`, Ollama in Compose) only when the final decision is
`allow` / `allow_with_modification` and no human review is pending. Only the **transformed** content
is sent; attachments and RAG documents are wrapped as delimited data. `requested_model` is the
logical destination the policies judge; the gateway serves it with the configured local models.

The answer passes `detection/output/guardrails.py` before the caller sees it:

| Finding in the answer | Action |
|---|---|
| PII / financial identifiers | `redacted` (`[REDACTED:TYPE]`) |
| Secrets (API key, password) | `blocked` |
| Attack payload aimed at the next consumer (answer scored as indirect content) | `blocked` |
| Disclosure of the gateway's system prompt | `blocked` |

A `model.response` event is appended to the tenant's hash chain with status, model, latency, token
usage, the answer's SHA-256 and the guard verdict, never the answer itself. Backend failures are
returned as `status: "error"` and audited; they never undo the recorded decision. With
`MODEL_BACKEND_URL` empty the gateway runs decision-only (`status: "not_forwarded"`).

## Dashboard (Phase 7)

React + Recharts served by nginx, calling `/v1/dashboard/summary` and `/v1/decisions` (both
tenant-scoped, `audit:read`), which return counts, labels and scores only. Pages: overview,
playground, decisions and review queue, policies, agents, audit trail with chain verification.
Tokens live in memory only.
