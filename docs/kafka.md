# Kafka

## Deployment

The `kafka` service runs a single-node **KRaft** cluster (`apache/kafka:3.9.0`): one process acts as both broker and controller, so there is **no ZooKeeper**. Settings:

| Setting | Value | Why |
|---|---|---|
| Listeners | `PLAINTEXT://kafka:9092` (clients), `CONTROLLER://:9093` (quorum) | Internal Docker network only; no host port is published |
| `auto.create.topics.enable` | `false` | Topics exist only via `infra/kafka/topics.sh`; a typo in a topic name fails instead of creating a stray topic |
| Replication factor | 1 | Single broker. **Local-development only:** a broker loss loses unflushed data |
| Heap | 512 MB (`KAFKA_HEAP_OPTS`) | Keeps the laptop memory budget |
| Data | named volume `kafkadata` | Survives container restarts |

## Topics

`kafka-init` creates the topics idempotently (`--if-not-exists`) and fails if an existing topic has fewer partitions than required.

| Topic | Partitions | Key | Retention | Producers | Consumers |
|---|---:|---|---|---|---|
| `governance.events.raw` | 6 | `tenant_id` | 7 days or 256 MiB per partition | event-publisher (gateway events), event-producer (synthetic) | Spark `validate` query, verifier |
| `governance.events.valid` | 6 | `tenant_id` | 3 days or 256 MiB per partition | Spark `validate` query | Spark aggregation queries |
| `governance.events.dead-letter` | 1 | none | 14 days or 256 MiB | Spark `validate` query (reason + coordinates, no payload) | Operators |

Every topic also sets `segment.bytes=64 MiB`, so the size caps take effect: Kafka deletes whole segments only. In the worst case the three topics use about 3.25 GiB.

**Key choice:**
- `tenant_id` keeps one tenant's events ordered within a partition and spreads load across partitions.
- `agent_id` was rejected because it is null for user and service callers, which would funnel them all into one partition.
- Aggregations use event time, not arrival order, so per-agent ordering isn't needed.

## Message format

| Part | Content |
|---|---|
| Value | UTF-8 JSON conforming to `schemas/governance_event/1.0.0.json` |
| Key | `tenant_id` |
| Header `schema_version` | `1.0.0` |
| Header `source` | `gateway` or `producer` |
| Header `event_type` | Gateway events only |
| Header `run_id` | Producer events only (identifies a backfill run) |
| Compression | gzip (stdlib codec on the client side) |

## Delivery semantics

```mermaid
sequenceDiagram
  participant G as gateway
  participant DB as postgres (event_outbox)
  participant P as event-publisher
  participant K as kafka
  G->>DB: BEGIN; decision ... + outbox row; COMMIT
  loop every 0.5 s (immediately while batches are full)
    P->>DB: SELECT due unpublished rows FOR UPDATE SKIP LOCKED LIMIT 500
    P->>K: send each (acks=all, idempotent producer)
    K-->>P: ack / error per message
    P->>DB: acked: published_at=now(); failed: attempts+1, next_attempt_at=now()+backoff
    P->>DB: COMMIT
  end
```

- **At-least-once.** A message is marked published only after Kafka acknowledges it. If the publisher dies between the ack and the commit, the row stays unpublished and is sent again. Downstream consumers deduplicate on `event_id`, which equals the decision id.
- **Retry.** Each failed row waits `min(base × 2^(attempts−1), max)`, minus up to 20% jitter. Defaults: base 1 s, max 300 s (`PUBLISHER_BACKOFF_*`). While Kafka is unreachable at start-up, the publisher retries the connection with the same backoff (capped at 30 s).
- **Several publishers** can run safely: `SKIP LOCKED` gives each one a disjoint batch.
- **Kafka down.** The gateway keeps deciding requests; events accumulate in `event_outbox` and drain once Kafka is back. The `OutboxBacklogGrowing` alert fires above 10k unpublished rows.

## Security

| Area | Controls / limits |
|---|---|
| Network | Kafka isn't reachable from the host. Only services on the Compose network can connect |
| Transport (**local limitation**) | PLAINTEXT with no authentication. For anything beyond a single developer machine, enable SSL or SASL_SSL listeners (`KAFKA_LISTENER_SECURITY_PROTOCOL_MAP`, keystores mounted read-only) plus ACLs: publisher and producer may write `raw`; Spark may read `raw` and write `valid` and `dead-letter` |
| Event content | Value-free by schema (no prompts, outputs, entity values, credentials, redaction maps); `caller_id` is an HMAC pseudonym |
| Kafka UI | Opt-in (`--profile tools`), bound to 127.0.0.1, configured read-only |
