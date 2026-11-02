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

## Production
Terminate TLS upstream; use managed Postgres/Redis; load secrets from Vault / AWS Secrets Manager / Azure Key Vault / Kubernetes Secrets; rotate DB, Redis and JWT keys; prefer an external OIDC provider with asymmetric keys.
