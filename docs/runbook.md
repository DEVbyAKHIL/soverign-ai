# Runbook

## Start / stop

```bash
docker compose up --build -d                 # whole stack incl. kafka, publisher, producer
make stream-up                               # only the streaming services (+ their dependencies)
docker compose stop event-producer           # pause synthetic traffic
docker compose down                          # stop everything; volumes (data) are kept
```

## Check health

```bash
docker compose ps                                            # all long-running services "healthy"
docker compose logs -f event-publisher event-producer        # JSON logs
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --describe
```

## Generate and verify 1,000,000 events

```bash
make verify-1m                 # TOTAL=1000000 by default; e.g. make verify-1m TOTAL=200000
```

The command runs in a one-shot `event-producer` container:
1. Backfills TOTAL events under a new `run_id`, spread over the last `PRODUCER_SPAN_HOURS` with at most `PRODUCER_DISORDER_S` of disorder.
2. Reads the whole `governance.events.raw` topic back and selects that run's messages by header.
3. Validates every event against schema 1.0.0 and checks its UTC timestamp.
4. Counts distinct `event_id`s and prints the summary.

The exit code is 1 if any check fails.

To exercise invalid and duplicate handling, run with injection turned on:

```bash
PRODUCER_FAILURE_RATE=0.01 PRODUCER_DUPLICATE_RATE=0.005 make verify-1m TOTAL=100000
```

The summary must show:
- invalid read back = invalid injected
- distinct ids = generated valid events

## Outbox

```sql
-- backlog and retry state
select count(*) filter (where published_at is null)  as unpublished,
       count(*) filter (where attempts > 1)          as retried,
       max(attempts)                                 as max_attempts
from event_outbox;
-- why rows are failing (exception class names only)
select last_error, count(*) from event_outbox where published_at is null group by 1;
```

| Situation | What happens / what to do |
|---|---|
| Kafka down | Gateway unaffected; backlog grows; publisher logs `outbox_publish_failed` and backs off. Start Kafka; backlog drains automatically |
| Publisher crashed mid-batch | Restart policy brings it back; unacknowledged or uncommitted rows are re-sent (duplicates are expected and deduplicated downstream by `event_id`) |
| Force an immediate retry | `update event_outbox set next_attempt_at = now() where published_at is null;` |
| Disk pressure on Kafka | Topic caps: 256 MiB per partition, 64 MiB segments (`infra/kafka/topics.sh`). Lower `PRODUCER_RATE` or stop `event-producer` |
| Reset the local Kafka cluster (deletes all events) | `docker compose down`, then `docker volume rm sovereign-ai-gateway_kafkadata`, then `docker compose up -d` |

Published outbox rows aren't deleted yet. A retention job is planned for a later phase; see `docs/risks-and-mitigations.md`.

## Tests

```bash
make test-docker               # full suite on Linux: DB tests + real-Kafka round trips
pytest tests/streaming         # host: schema, producer, publisher units (no DB/Kafka needed)
```

## Spark processor (phase S2)

```bash
docker compose logs -f spark-stream-processor          # JSON lines: processor_started, batch, query_failed
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c   "select query_name, max(batch_id), sum(input_rows) from pipeline_progress group by 1"'
python scripts/verify_pipeline.py --total 1000000       # end-to-end + restart test (make verify-pipeline)
docker compose --profile test run --rm spark-tests      # aggregation window maths in the Spark image
```

| Situation | What happens / what to do |
|---|---|
| Processor restarts in a loop | `docker compose logs spark-stream-processor` - look for `query_failed` and the first `Caused by`. Postgres or Kafka outages heal on their own (batch replay) |
| Change a query's logic or window | Spark state is tied to the query plan: stop the processor, remove that query's directory from the `sparkcheckpoints` volume, start again (it re-reads `valid` from the earliest retained offset; upserts overwrite) |
| Full reprocess | `docker compose stop spark-stream-processor`, `docker volume rm sovereign-ai-gateway_sparkcheckpoints`, truncate `aggregated_*`, `pipeline_*`, start the processor |
| Inspect dead letters | `docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:9092 --topic governance.events.dead-letter --from-beginning --max-messages 20` (reasons and coordinates only, no payloads) |
| Events older than the watermark | Not aggregated by design (5 min, `STREAM_WATERMARK`); exported as `stream_rows_dropped_by_watermark` on /metrics. Do not re-enable `STREAM_MAX_OFFSETS_PER_TRIGGER` without raising the watermark |

## Reporting API and live dashboard (phase S3)

| Situation | What to do |
|---|---|
| Dashboard pages show "No data in this range yet" | Check that `event-producer` is running (it feeds the demo tenant `aaaaaaaa-0000-4000-8000-000000000001`) and that `spark-stream-processor` is healthy. Aggregates appear within one trigger (~5 s) after Spark processes the events |
| 401 / 403 from `/reporting/...` | The token is missing or expired (gateway tokens last 15 min), or the role lacks `audit:read` (use `admin` or `security_analyst`) |
| 503 on `/readyz` | The database is unreachable, or `REPORTING_DB_PASSWORD` changed after migration 0008. Re-run `docker compose run --rm migrate` to reset the role password |
| "(stale)" next to the freshness time | The processor hasn't written this tenant's data for more than 2 minutes. Check `docker compose logs spark-stream-processor` |

## Data retention

```bash
docker compose --profile ops run --rm retention --dry-run                  # what would be deleted
docker compose --profile ops run --rm retention --archive-dir /archive     # delete (archive audit first)
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c   "select target, rows_deleted, dry_run, finished_at from retention_runs order by id desc limit 20"'
```

- **Schedule:** daily, at low traffic.
  - Linux: a cron line such as `15 3 * * * cd /srv/gateway && docker compose --profile ops run --rm retention`.
  - Windows: Task Scheduler with the same command.
- **Periods:** `RETENTION_*_DAYS`, `AUDIT_RETENTION_DAYS` and `KAFKA_*_RETENTION_HOURS` in `.env`.
- **Kafka retention changes** take effect on the next `docker compose up` (kafka-init re-applies them).
- **After an audit prune:** `GET /v1/audit/verify` must still return `valid: true`, because verification starts from `audit_chain_anchors`.
- **Archives** are written to the `retentionarchive` volume (gzip JSONL); move them to cold storage if your policy requires.

## Secrets and database logins

| Task | Procedure |
|---|---|
| New clone | `python scripts/init_env.py` |
| After `git pull` | `python scripts/init_env.py --add-missing`, then `docker compose up -d` (`migrate` creates any new roles) |
| Check | `python scripts/init_env.py --check` (values are never printed) |
| Rotate a DB password | Change the `*_DB_PASSWORD` value in `.env` (or the secret manager), then `docker compose run --rm migrate` (alters the role), then `docker compose up -d --force-recreate <service>` |
| Rotate `JWT_SECRET` | Change it and restart gateway + reporting-api. Existing tokens become invalid (max TTL 1 h) |
| Rotate `FINGERPRINT_HMAC_KEY` | Change it and restart the gateway. Older fingerprints no longer match new ones; repeat detection resets for its 10-minute window |
| Rotate `ENTITY_HASH_KEY` | As above. Old entity hashes and pseudonyms become unlinkable (crypto-shredding) |

## Fresh-clone reproducibility check

```bash
docker compose down                                   # free the ports (data kept)
git clone https://github.com/DEVbyAKHIL/soverign-ai.git /tmp/clean && cd /tmp/clean
python scripts/init_env.py
docker compose -p clean up --build -d && docker compose -p clean ps
docker compose -p clean --profile test run --rm tests
python scripts/verify_pipeline.py --total 1000000     # the script drives the default project:
                                                      # run it from the clone directory with COMPOSE_PROJECT_NAME=clean
docker compose -p clean down -v                       # remove the test stack and its volumes
```

Recorded result: `EVALUATION.md`, section "Fresh-clone reproducibility".
