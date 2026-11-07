"""Event fields as (name, Spark SQL type) - mirrors schemas/governance_event/1.0.0.json.

Kept free of pyspark imports so a host test can check parity with the JSON Schema.
`metadata` is deliberately not parsed: aggregations do not use it, and its mixed value types
(bool, number, string) would not fit a single Spark map type.
"""

NOT_PARSED = frozenset({"metadata"})

EVENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("schema_version", "string"),
    ("event_id", "string"),
    ("event_timestamp", "string"),
    ("tenant_id", "string"),
    ("agent_id", "string"),
    ("caller_type", "string"),
    ("caller_id", "string"),
    ("event_type", "string"),
    ("decision", "string"),
    ("action", "string"),
    ("destination_model", "string"),
    ("destination_jurisdiction", "string"),
    ("declared_purpose", "string"),
    ("sensitivity_level", "string"),
    ("pii_count", "int"),
    ("injection_risk_score", "double"),
    ("policy_ids", "array<string>"),
    ("latency_ms", "int"),
    ("tokens_processed", "bigint"),
    ("status", "string"),
)


def ddl() -> str:
    """DDL string accepted by pyspark.sql.functions.from_json."""
    return ", ".join(f"`{name}` {type_}" for name, type_ in EVENT_FIELDS)
