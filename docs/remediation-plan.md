# Remediation plan

Derived from `docs/compliance-audit.md` (2026-11-07).

**Effort scale:**

| Effort | Duration |
|---|---|
| XS | ≤ 1 h |
| S | ≤ ½ day |
| M | 1-2 days |
| L | 3+ days |

**Order:** Critical, then High, then Medium, then Low. Within a group, follow the numbering.

## Critical blockers (the portfolio cannot be submitted without these)

| # | Problem | Evidence | Required change | Files | Effort | Acceptance test | Depends on |
|---|---|---|---|---|---|---|---|
| C1 | Phase S3 is uncommitted; no streaming code is on GitHub | `git status` lists `reporting/`, `frontend/src/pages/live/`, migration 0008 as untracked; remote `main` = `530252d` | Commit S3; push `feat/stream-phase1`; open a PR; CI green; merge to `main` | Git only | XS | AT-T2-11 | - |
| C2 | No architecture PNG | No `*.png` in the repository | Render `DATA_FLOW.md` / `ARCHITECTURE.md` Mermaid to PNG; add remarks | `docs/portfolio/architecture.png`, `phase-1-conception.md` | S | AT-G03 | - |
| C3 | No phase structure or documents | `ls docs` | Create the three phase documents with the required headings. Student-authored text; the repository supplies the facts | `docs/portfolio/phase-{1,2,3}-*.md`, `README.md` link | M (student) | AT-G01, AT-G04 | C2 |
| C4 | No GitHub link `.txt` (phase 2 and final) | No `*.txt` | One-line `.txt` with the repository URL, after C1 | `docs/portfolio/github-link.txt` (or uploaded separately) | XS | AT-G05 | C1 |
| C5 | No 2-page PDF abstract or 50-word abstract | No PDF | Student writes them; produce the PDF from an A4 / Arial / 2 cm / 1.5 / justified / hyphenated template | PDF (submitted; optionally `docs/portfolio/`) | M (student) | AT-G06, AT-G07 | C3 |

## High-priority Task 2 gaps

| # | Problem | Evidence | Required change | Files | Effort | Acceptance test | Depends on |
|---|---|---|---|---|---|---|---|
| H1 | Clean-clone reproducibility unproven; `.env` creation is manual | Works only on this machine; 7 secrets set by hand | `scripts/init_env.py` generates `.env` from `.env.example` with random secrets. Run a clean-clone test and record it | `scripts/init_env.py`, `README.md`, `EVALUATION.md` | S | AT-T2-13 | C1 |
| H2 | CI does not run the Kafka tests; only the gateway image and `requirements.txt` are scanned | `ci.yml` has no Kafka service; trivy `image-ref: gateway:ci` (line 87); pip-audit on `requirements.txt` only | Add a Kafka service to the python job (KRaft image, `KAFKA_TEST_BOOTSTRAP`); loop Trivy over the 4 images; pip-audit every `requirements-*.txt` | `.github/workflows/ci.yml` | S | AT-R06 | C1 |
| H3 | Retention not enforced | `gateway/config.py:29` unused; outbox and aggregates grow without bound | `python -m ops.retention`: delete published outbox rows older than N days and aggregates older than N days; archive and verify audit chains before any audit pruning; document it | new `ops/retention.py`, migration for an index if needed, `docs/privacy.md`, tests | M | AT-T2-09 | - |
| H4 | Plain SHA-256 fingerprints of content and output | `gateway/services/decisions.py:198`, `gateway/services/forwarding.py:43` | Keyed HMAC with a purpose-derived subkey of `ENTITY_HASH_KEY`. Repeat detection in `agents/features.py` keeps working, because only equality is compared | `gateway/services/decisions.py`, `forwarding.py`, tests | S | AT-T2-10 | - |
| H5 | Gateway trusts the client's model and jurisdiction | `gateway/services/decisions.py:146,148,234-235`; `AIAsset` unused | Phase 4: resolve endpoint, jurisdiction and allowed purposes from `ai_assets`; reject unknown, inactive or unapproved assets; seed the registry; update policies and tests | `gateway/services/decisions.py`, new `gateway/services/registry.py`, a migration (purposes/status columns), seed, tests | M | AT-A02 | - |
| H6 | Least privilege incomplete: the publisher uses the app DB login | `docker-compose.yml` event-publisher `DATABASE_URL` | Migration creates `outbox_publisher` (SELECT/UPDATE on `event_outbox` only); Compose and `.env` | migration 0009, `docker-compose.yml`, `.env.example`, test | S | AT-T2-08 | - |
| H7 | Stale planning documents contradict the implementation | `docs/architecture-after-refactor.md:3`, `docs/refactor-plan.md:9`, 2-minute / 200k values | Mark them "historical plan (2026-11-06)" with a pointer to the current docs, or update the values | those 2 files, `docs/implementation-order.md` | XS | AT-R09 | - |
| H8 | SECURITY.md / privacy.md don't cover streaming; THREAT_MODEL.md missing | `SECURITY.md` has 1 Kafka mention; `docs/privacy.md` has none | Add a streaming section (Kafka exposure, DB roles, event minimisation, DLQ, suppression); STRIDE threat model per trust boundary | `SECURITY.md`, `docs/privacy.md`, new `THREAT_MODEL.md` | S | AT-A09 | - |

## Medium-priority improvements

| # | Problem | Required change | Files | Effort | Acceptance test |
|---|---|---|---|---|---|
| M1 | Scalability shown only by design | Document the scale-out path; record one experiment (2 publishers, or Spark `local[8]`) with throughput | `docs/scalability.md`, `EVALUATION.md` | S | AT-T2-06 |
| M2 | End-to-end check stops at storage | `verify_pipeline.py` also queries the reporting API for the run's tenants and compares totals | `scripts/verify_pipeline.py` | S | AT-S11 |
| M3 | Observability gaps | Prometheus scrape jobs for reporting-api and the processor; alerts on `stream_query_active == 0`, `stream_rows_dropped_by_watermark > 0`, outbox backlog | `infra/monitoring/*.yml` | S | AT-R07 |
| M4 | Base images not digest-pinned | `FROM image@sha256:…` in every Dockerfile and Compose image | `infra/docker/*`, `docker-compose.yml` | S | AT-T2-12 |
| M5 | Output guard lacks canary and cross-tenant checks | Per-tenant canary marker; detect another tenant's pseudonyms; quarantine action | `detection/output/guardrails.py`, tests | M | AT-A06 |
| M6 | `tools.md` with rationale (also feeds the portfolio) | One row per tool | `docs/portfolio/tools.md` | S | AT-G10 |

## Low-priority polish

| # | Item | Effort |
|---|---|---|
| L1 | Migration downgrade round-trip test (AT-R03) | S |
| L2 | Paragraph in `docs/kafka.md` on why Spark checkpoint offsets replace consumer-group commits (AT-S03) | XS |
| L3 | Screenshots of the 7 live pages kept as evidence (AT-S10) | XS |
| L4 | Decide whether to commit `docs/project-explained.html` | XS |
| L5 | Python type checking (mypy) in CI | M |
| L6 | Fix the repository name typo ("soverign") before submission, if allowed | XS |
| L7 | Ollama under an opt-in profile to save about 3 GB RAM during streaming runs | XS |
