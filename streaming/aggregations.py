"""Windowed aggregations (docs/aggregations.md explains each window choice).

Every spec is a pure DataFrame -> DataFrame transformation, so the same code runs on a streaming
DataFrame in production and on a static DataFrame in tests.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F  # noqa: N812

from streaming.validation import ALL_AGENTS

TS_FORMAT = "yyyy-MM-dd'T'HH:mm:ss.SSSX"


@dataclass(frozen=True)
class Output:
    table: str
    metric: str
    column: str  # column of the aggregated frame holding the value
    fixed: dict[str, str] = field(default_factory=dict)  # constant dimension values


@dataclass(frozen=True)
class AggSpec:
    name: str
    length: str
    slide: str | None  # None = tumbling
    keys: tuple[str, ...]
    aggs: Callable[[], dict[str, Column]]
    outputs: tuple[Output, ...]
    agents_only: bool = False
    derived: Callable[[], dict[str, Column]] = dict

    @property
    def window_type(self) -> str:
        return "sliding" if self.slide else "tumbling"


def parse(json_col: Column, ddl: str) -> Column:
    return F.from_json(json_col, ddl)


def enrich(df: DataFrame) -> DataFrame:
    """Typed event time plus 0/1 indicator columns used by the aggregations."""
    is_ = lambda c: c.cast("int")  # noqa: E731
    return (
        df.withColumn("event_ts", F.to_timestamp("event_timestamp", TS_FORMAT))
        .withColumn("is_deny", is_(F.col("decision").isin("deny", "quarantine")))
        .withColumn("is_injection", is_(F.col("injection_risk_score") >= 0.6))
        .withColumn("has_pii", is_(F.col("pii_count") > 0))
        .withColumn("is_failed", is_(F.col("status") == "failed"))
        .withColumn("is_risky_destination", is_(F.col("destination_jurisdiction") != F.lit("eu")))
    )


def prepare(parsed: DataFrame, watermark: str) -> DataFrame:
    """Enrich, set the event-time watermark and drop duplicate event_ids (at-least-once input).

    Duplicates are tracked until the watermark passes them; the same event delivered twice within
    that horizon is counted once."""
    return (
        enrich(parsed)
        .withWatermark("event_ts", watermark)
        .dropDuplicatesWithinWatermark(["event_id"])
    )


def aggregate(df: DataFrame, spec: AggSpec) -> DataFrame:
    window = (
        F.window("event_ts", spec.length, spec.slide)
        if spec.slide
        else F.window("event_ts", spec.length)
    )
    if spec.agents_only:
        df = df.filter(F.col("agent_id").isNotNull())
    aggs = spec.aggs()
    out = df.groupBy(window.alias("w"), *spec.keys).agg(
        F.count(F.lit(1)).alias("event_count"), *[c.alias(n) for n, c in aggs.items()]
    )
    for name, col in spec.derived().items():
        out = out.withColumn(name, col)
    return out.select(
        F.col("w.start").alias("window_start"),
        F.col("w.end").alias("window_end"),
        *spec.keys,
        "event_count",
        *aggs,
        *spec.derived(),
    )


R, P, L = "aggregated_request_metrics", "aggregated_policy_metrics", "aggregated_latency_metrics"
PII, INJ = "aggregated_pii_metrics", "aggregated_injection_metrics"
RISK, SOV = "aggregated_agent_risk_metrics", "aggregated_sovereignty_metrics"
ALL = {"agent_id": ALL_AGENTS}


def _risk_score() -> Column:
    """Transparent heuristic in [0, 1] (not the trained Isolation Forest, see docs/aggregations)."""
    raw = (
        0.35 * F.col("deny_rate")
        + 0.35 * F.least(F.lit(1.0), 2 * F.col("injection_rate"))
        + 0.15 * F.col("pii_rate")
        + 0.15 * F.col("risky_destination_share")
    )
    return F.round(F.least(F.lit(1.0), raw), 4)


SPECS: tuple[AggSpec, ...] = (
    AggSpec(
        "requests_per_tenant_1m",
        "1 minute",
        None,
        ("tenant_id",),
        lambda: {"tokens": F.sum("tokens_processed")},
        (Output(R, "requests", "event_count", ALL), Output(R, "tokens", "tokens", ALL)),
    ),
    AggSpec(
        "requests_per_agent_1m",
        "1 minute",
        None,
        ("tenant_id", "agent_id"),
        lambda: {"tokens": F.sum("tokens_processed")},
        (Output(R, "requests", "event_count"), Output(R, "tokens", "tokens")),
        agents_only=True,
    ),
    AggSpec(
        "decisions_by_type_1m",
        "1 minute",
        None,
        ("tenant_id", "decision"),
        dict,
        (Output(P, "count", "event_count"),),
    ),
    AggSpec(
        "tenant_health_5m",
        "5 minutes",
        "1 minute",
        ("tenant_id",),
        lambda: {
            "deny_rate": F.avg("is_deny"),
            "failed": F.sum("is_failed"),
            "avg_ms": F.avg("latency_ms"),
            "p99_ms": F.percentile_approx("latency_ms", 0.99, 1000),
            "max_ms": F.max("latency_ms"),
        },
        (
            Output(P, "deny_rate", "deny_rate", {"decision": "*"}),
            Output(P, "failed_evaluations", "failed", {"decision": "*"}),
            Output(L, "avg_ms", "avg_ms"),
            Output(L, "p99_ms", "p99_ms"),
            Output(L, "max_ms", "max_ms"),
        ),
    ),
    AggSpec(
        "pii_per_model_5m",
        "5 minutes",
        None,
        ("tenant_id", "destination_model"),
        lambda: {"pii_entities": F.sum("pii_count"), "events_with_pii": F.sum("has_pii")},
        (
            Output(PII, "pii_entities", "pii_entities"),
            Output(PII, "events_with_pii", "events_with_pii"),
        ),
    ),
    AggSpec(
        "injections_per_agent_5m",
        "5 minutes",
        "1 minute",
        ("tenant_id", "agent_id"),
        lambda: {
            "attempts": F.sum("is_injection"),
            "avg_score": F.avg("injection_risk_score"),
            "max_score": F.max("injection_risk_score"),
        },
        (
            Output(INJ, "attempts", "attempts"),
            Output(INJ, "avg_score", "avg_score"),
            Output(INJ, "max_score", "max_score"),
        ),
        agents_only=True,
    ),
    AggSpec(
        "agent_risk_10m",
        "10 minutes",
        "1 minute",
        ("tenant_id", "agent_id"),
        lambda: {
            "deny_rate": F.avg("is_deny"),
            "injection_rate": F.avg("is_injection"),
            "pii_rate": F.avg("has_pii"),
            "risky_destination_share": F.avg("is_risky_destination"),
            "tokens": F.sum("tokens_processed"),
        },
        (
            Output(RISK, "anomaly_score", "anomaly_score"),
            Output(RISK, "requests", "event_count"),
            Output(RISK, "deny_rate", "deny_rate"),
            Output(RISK, "injection_rate", "injection_rate"),
            Output(RISK, "pii_rate", "pii_rate"),
            Output(RISK, "risky_destination_share", "risky_destination_share"),
        ),
        agents_only=True,
        derived=lambda: {"anomaly_score": _risk_score()},
    ),
    AggSpec(
        "sovereignty_10m",
        "10 minutes",
        None,
        ("tenant_id", "destination_jurisdiction", "sensitivity_level"),
        lambda: {"tokens": F.sum("tokens_processed"), "pii_entities": F.sum("pii_count")},
        (
            Output(SOV, "events", "event_count"),
            Output(SOV, "tokens", "tokens"),
            Output(SOV, "pii_entities", "pii_entities"),
        ),
    ),
)


def to_rows(spec: AggSpec, records, batch_id: int) -> dict[str, list[tuple]]:
    """Wide aggregate rows -> long-format sink rows, grouped by table."""
    from streaming.sink import TABLE_DIMS, utc

    out: dict[str, list[tuple]] = {}
    for r in records:
        d = r.asDict()
        for o in spec.outputs:
            value = d[o.column]
            if value is None:
                continue
            dims = [o.fixed.get(name, d.get(name)) for name, _ in TABLE_DIMS[o.table]]
            out.setdefault(o.table, []).append(
                (
                    utc(d["window_start"]),
                    utc(d["window_end"]),
                    spec.window_type,
                    d["tenant_id"],
                    *dims,
                    o.metric,
                    float(value),
                    int(d["event_count"]),
                    spec.name,
                    batch_id,
                )
            )
    return out
