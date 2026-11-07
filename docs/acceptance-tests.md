# Acceptance tests

Run from the repository root, with Docker running and `.env` present. Commands use plain `docker compose` / Python, so they work without `make`.

**Evidence to retain:** save the console output of each run to `docs/portfolio/evidence/<test-id>-<date>.txt`.

## Task 2, critical

### AT-S02 · 1,000,000 events end to end with a processor restart

| Field | Value |
|---|---|
| Requirement | T2: ingest, process and aggregate ≥ 1M timestamped events; S-12 / S-13 recovery |
| Preconditions | Stack up: `docker compose ps` shows all services healthy |
| Command | `python scripts/verify_pipeline.py --total 1000000` (host Python with `requirements-dev.txt` installed) |
| Steps | The script backfills under a fresh run id and seed, kills `spark-stream-processor` at 40%, restarts it, waits for stable aggregates, then reconciles |
| Pass criteria | `Events produced = Events consumed = Events aggregated = 1,000,000`; `Dead-letter events: 0`; `Late (dropped by watermark): 0`; all `[PASS]` lines; `Processor restart recovery: PASSED`; exit code 0 |
| Last result | **PASSED** on 2026-11-07, run `p2-5c60e9904b` (`EVALUATION.md`) |

### AT-S01 · Ingestion read-back

| Field | Value |
|---|---|
| Command | `docker compose run --rm --build event-producer python -m producer verify-1m --total 1000000` |
| Pass criteria | `RESULT: PASSED`; produced = read back = schema-valid = distinct `event_id`s = 1,000,000 |
| Last result | PASSED, run `f93b1dfdd901` |

### AT-S05 · Dead-letter routing at scale

| Field | Value |
|---|---|
| Command | `PRODUCER_FAILURE_RATE=0.01 python scripts/verify_pipeline.py --total 100000 --no-restart` |
| Pass criteria | Dead-letter ≈ 1% (equal to the producer's injected count); `valid + dead-letter == consumed`; aggregated = valid |
| Last result | **NOT RUN at scale.** Covered by unit tests only |

### AT-S04/S06/S07 · Spark window maths

| Field | Value |
|---|---|
| Command | `docker compose --profile test run --rm --build spark-tests` |
| Pass criteria | `11 passed` (tumbling counts, sliding overlap ×5, deny rate, P99 sample, streaming dedup) |
| Last result | 11 passed |

### AT-S08 · Idempotent storage and least privilege

| Field | Value |
|---|---|
| Command | `docker compose --profile test run --rm --build tests pytest -q --no-cov tests/integration/test_aggregate_sink.py` |
| Pass criteria | All pass, including `test_replaying_a_batch_does_not_duplicate_rows` and `test_processor_role_is_least_privilege` |

### AT-S09 · Reporting API

| Field | Value |
|---|---|
| Command | `docker compose --profile test run --rm tests pytest -q --no-cov tests/reporting` |
| Pass criteria | 11 passed (auth 401/403, tenant isolation, ranges 422, pagination, suppression, overview, OpenAPI) |
| Live check | Sign in at http://127.0.0.1:5173 as `security_analyst` → **Real-time (streaming)** → the overview shows a refresh time under 10 s old and non-zero requests |

### AT-S10 · Dashboard pages

| Field | Value |
|---|---|
| Steps | Open each of the 7 live pages |
| Pass criteria | Each renders data or "No data in this range yet"; no console errors |
| Evidence | Screenshot each page |

### AT-S12 · Outbox atomicity and ack-gated publishing

| Field | Value |
|---|---|
| Command | `docker compose --profile test run --rm tests pytest -q --no-cov tests/integration/test_event_outbox.py tests/integration/test_outbox_publisher.py tests/integration/test_kafka_roundtrip.py` |
| Pass criteria | All pass; the Kafka tests are **executed, not skipped** (the `KAFKA_TEST_BOOTSTRAP` env var is set by the `tests` service) |

### AT-T2-11 · Code on GitHub

| Field | Value |
|---|---|
| Commands | `git status --short` (empty apart from deliberately untracked files); `git ls-remote origin main`; on GitHub, `main` shows `streaming/`, `producer/`, `publisher/`, `reporting/`, `frontend/src/pages/live/` |
| Pass criteria | All of the above, plus the latest CI run on `main` is green |
| Current result | **FAIL** (S3 uncommitted, nothing pushed) |

### AT-T2-13 · Clean-clone reproducibility

| Field | Value |
|---|---|
| Steps | 1. `git clone <repo> /d/filefactory/clean && cd /d/filefactory/clean`<br>2. Create `.env` from `.env.example` (`scripts/init_env.py` once H1 is done)<br>3. `docker compose -p clean up -d --build` (stop the main stack first; ports clash)<br>4. Wait for health<br>5. `python scripts/verify_pipeline.py --total 100000` |
| Pass criteria | All services healthy within 10 minutes; the verification passes |
| Current result | **NOT RUN** |

## Security, governance and protection

| ID | Requirement | Command / check | Pass criteria |
|---|---|---|---|
| AT-T2-08 | Least privilege, scanning | `docker compose exec postgres psql … -c "set role reporting_ro; select 1 from decisions"`; CI trivy job | `permission denied`; CI scans 4 images (after H2) |
| AT-T2-09 | Retention enforced | `python -m ops.retention --dry-run` then a real run (after H3) | Only rows older than the configured days are removed; audit verify still `valid: true` |
| AT-T2-10 | No plain hashes of content | `grep -n "hashlib.sha256" gateway/services/*.py` (after H4) | No matches on content or output; a test shows the fingerprint depends on the key |
| AT-A02 | Registry trust | `pytest tests/integration/test_registry.py` (after H5) | A spoofed `destination_jurisdiction` is ignored; an unknown or inactive model is denied and audited |
| AT-R06 | CI coverage | GitHub Actions run on `main` | Green; the Kafka round-trip tests execute; pip-audit covers 5 requirement files |

## Portfolio

| ID | Check | Pass criteria |
|---|---|---|
| AT-G01 / AT-G04 | `ls docs/portfolio/` | The three phase documents exist with the headings from `portfolio-guidelines-compliance.md` |
| AT-G03 | Open `docs/portfolio/architecture.png` | All services and topics visible; remarks below it |
| AT-G05 | Open the `.txt` | One URL; it opens a `main` that contains the streaming code |
| AT-G06 / AT-G07 | `pdfinfo abstract.pdf`; `pdffonts abstract.pdf` | Pages ≤ 2; page size 595×842 pt; Arial embedded; visual check of margins, spacing, justification and hyphenation |
