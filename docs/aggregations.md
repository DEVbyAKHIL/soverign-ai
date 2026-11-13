# Aggregations

All aggregations run in `spark-stream-processor` (`streaming/aggregations.py`, `SPECS`), one streaming query per spec, on **event time** (`event_timestamp`, UTC).

Before aggregating, every query:
1. parses the event;
2. adds 0/1 indicator columns (`is_deny`, `is_injection`, `has_pii`, `is_failed`, `is_risky_destination`);
3. sets a **5-minute watermark**;
4. drops duplicate `event_id`s within that watermark (`dropDuplicatesWithinWatermark`). Delivery is at-least-once, so the same event can arrive twice.

| # | Requirement | Spec (query name) | Window | Group by | Metrics → table |
|---|---|---|---|---|---|
| 1 | Requests per tenant | `requests_per_tenant_1m` | tumbling 1 min | tenant | `requests`, `tokens` → `aggregated_request_metrics` (agent = nil UUID, meaning "all") |
| 2 | Requests per agent | `requests_per_agent_1m` | tumbling 1 min | tenant, agent (agent callers only) | `requests`, `tokens` → `aggregated_request_metrics` |
| 3 | Policy decisions by type | `decisions_by_type_1m` | tumbling 1 min | tenant, decision | `count` → `aggregated_policy_metrics` |
| 4 | Deny rate | `tenant_health_5m` | sliding 5 min / 1 min | tenant | `deny_rate` (deny + quarantine share) → `aggregated_policy_metrics` (decision `*`) |
| 5 | Average gateway latency | `tenant_health_5m` | sliding 5 min / 1 min | tenant | `avg_ms`, `max_ms` → `aggregated_latency_metrics` |
| 6 | P99 gateway latency | `tenant_health_5m` | sliding 5 min / 1 min | tenant | `p99_ms` = `percentile_approx(latency_ms, 0.99, 1000)` → `aggregated_latency_metrics` |
| 7 | PII detections per model | `pii_per_model_5m` | tumbling 5 min | tenant, destination model | `pii_entities`, `events_with_pii` → `aggregated_pii_metrics` |
| 8 | Injection attempts per agent | `injections_per_agent_5m` | sliding 5 min / 1 min | tenant, agent | `attempts` (score ≥ 0.6), `avg_score`, `max_score` → `aggregated_injection_metrics` |
| 9 | Agent anomaly score | `agent_risk_10m` | sliding 10 min / 1 min | tenant, agent | `anomaly_score` plus its inputs → `aggregated_agent_risk_metrics` |
| 10 | Sensitive data volume by jurisdiction | `sovereignty_10m` | tumbling 10 min | tenant, jurisdiction, sensitivity | `events`, `tokens`, `pii_entities` → `aggregated_sovereignty_metrics` |
| 11 | Failed policy evaluations | `tenant_health_5m` | sliding 5 min / 1 min | tenant | `failed_evaluations` (status `failed`) → `aggregated_policy_metrics` (decision `*`) |

Rows 4, 5, 6 and 11 share the same window and grouping, so a single query computes them and writes to two tables. That gives **8 aggregation queries** plus the validation query.

## Why these windows

- **Tumbling** windows don't overlap, so each event is counted exactly once. They suit **counts per period** (load, decisions, PII exposure, sovereignty volume) and allow reconciliation: the sum over all 1-minute windows equals the number of valid events. The end-to-end verification relies on this.
- **Sliding** windows give a value that refreshes every minute over a longer horizon. They suit **rates and percentiles** that need enough samples to be stable (deny rate, average and P99 latency, failure rate) and **burst detection** (injection attempts), where a burst straddling a tumbling boundary would otherwise be split in two.

Choice of window length:

| Length | Used for | Reason |
|---|---|---|
| 1 minute | Operational counts | Matches how load and decisions are watched live |
| 5 minutes | Rates, P99, PII | A P99 needs roughly 100+ samples to mean anything. PII is sparse, so 1-minute windows would be mostly zero |
| 10 minutes | Agent behaviour, sovereignty | The same 10-minute horizon as the gateway's per-request agent features (`agents/features.py`). Sovereignty is a reporting view, not an alert |

## Watermark and late data

| Setting | Value and reason |
|---|---|
| Watermark | 5 minutes (`STREAM_WATERMARK`). Covers producer disorder (live mode ≤ 30 s), the outbox relay delay (seconds) and event-time skew between partitions while the processor catches up after a restart. The first 1M run used 2 minutes with a 200k offset cap and lost 8.4% of events as late (see `EVALUATION.md`) |
| Events later than the watermark | Dropped by Spark and **not** aggregated. Spark reports them per query (`numRowsDroppedByWatermark` in `stateOperators`) |
| Output mode | `update`: every micro-batch emits the current value of each window it changed. The sink upserts on the primary key `(window_start, window_end, dimensions, metric)`, so a re-emitted or replayed row overwrites rather than duplicates |

## The anomaly score is a heuristic

```
anomaly_score = min(1, 0.35·deny_rate + 0.35·min(1, 2·injection_rate) + 0.15·pii_rate + 0.15·risky_destination_share)
```

This is a transparent, explainable risk heuristic over the 10-minute window. It is **not** the gateway's trained Isolation Forest (`agents/anomaly_detector.py`), which needs a 7-day per-agent baseline that a 10-minute stream window does not have. Scoring windows with that model is possible future work and would need its feature set rebuilt from stream state.
