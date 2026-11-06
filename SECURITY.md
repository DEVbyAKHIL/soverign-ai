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
Terminate TLS upstream; use managed Postgres/Redis; load secrets from Vault / AWS Secrets Manager / Azure Key Vault / Kubernetes Secrets; rotate DB, Redis and JWT keys; prefer an external OIDC provider with asymmetric keys.
