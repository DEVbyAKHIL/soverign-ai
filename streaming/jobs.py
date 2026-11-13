"""Streaming queries: one validation query (raw -> valid / dead-letter) and one query per
aggregation spec (valid -> PostgreSQL). Each query has its own checkpoint directory."""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F  # noqa: N812
from pyspark.sql.streaming import StreamingQuery
from pyspark.sql.types import StringType

from events.schema import TOPIC_DEAD_LETTER, TOPIC_RAW, TOPIC_VALID
from streaming.aggregations import SPECS, AggSpec, aggregate, parse, prepare, to_rows
from streaming.config import ProcessorConfig
from streaming.fields import ddl
from streaming.log import log
from streaming.sink import PostgresSink
from streaming.validation import invalid_reason


def _source(spark: SparkSession, cfg: ProcessorConfig, topic: str) -> DataFrame:
    reader = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", cfg.kafka_bootstrap)
        .option("subscribe", topic)
        .option("startingOffsets", cfg.starting_offsets)
        .option("failOnDataLoss", "false")  # retention may delete unread data: log, don't stop
        .option("includeHeaders", "true")
    )
    # Off by default. Spark splits the cap across partitions by offset count; when partitions
    # carry different event rates, the low-volume ones run ahead in event time, the watermark
    # follows them, and the busy partitions' events are dropped as late (measured: 8.4% of a 1M
    # run with a 200k cap and a 2-minute watermark).
    if cfg.max_offsets_per_trigger > 0:
        reader = reader.option("maxOffsetsPerTrigger", cfg.max_offsets_per_trigger)
    return reader.load()


def _kafka_write(df: DataFrame, cfg: ProcessorConfig, topic: str) -> None:
    (
        df.write.format("kafka")
        .option("kafka.bootstrap.servers", cfg.kafka_bootstrap)
        .option("kafka.acks", "all")
        .option("topic", topic)
        .save()
    )


def validation_query(spark: SparkSession, cfg: ProcessorConfig, sink: PostgresSink):
    reason = F.udf(invalid_reason, StringType())
    raw = _source(spark, cfg, TOPIC_RAW)
    run_id = F.expr("filter(headers, h -> h.key = 'run_id')[0].value").cast("string")
    staged = raw.select(
        "key",
        "value",
        "headers",
        "topic",
        "partition",
        "offset",
        reason("value").alias("reason"),
        F.coalesce(run_id, F.lit("")).alias("run_id"),
    )

    def handle(batch: DataFrame, batch_id: int) -> None:
        batch.persist()
        try:
            _kafka_write(
                batch.filter(F.col("reason").isNull()).select("key", "value", "headers"),
                cfg,
                TOPIC_VALID,
            )
            # Dead letter: reason + coordinates of the original record on the raw topic. The
            # payload itself is not copied (it may contain anything an upstream bug put there).
            dlq = batch.filter(F.col("reason").isNotNull()).select(
                F.col("key"),
                F.to_json(
                    F.struct(
                        F.col("reason"),
                        F.get_json_object(F.col("value").cast("string"), "$.event_id").alias(
                            "event_id"
                        ),
                        F.col("topic").alias("source_topic"),
                        F.col("partition").alias("source_partition"),
                        F.col("offset").alias("source_offset"),
                        F.col("run_id"),
                        F.date_format(F.current_timestamp(), "yyyy-MM-dd'T'HH:mm:ss.SSSX").alias(
                            "failed_at"
                        ),
                    )
                ).alias("value"),
            )
            _kafka_write(dlq, cfg, TOPIC_DEAD_LETTER)
            counts = (
                batch.groupBy("run_id")
                .agg(
                    F.count(F.lit(1)).alias("consumed"),
                    F.sum(F.col("reason").isNull().cast("long")).alias("valid"),
                    F.sum(F.col("reason").isNotNull().cast("long")).alias("invalid"),
                )
                .collect()
            )
            total = sum(r["consumed"] for r in counts)
            valid = sum(r["valid"] for r in counts)
            sink.write(
                {},
                progress=("validate", batch_id, total, valid, total - valid, valid),
                run_counts=[
                    (r["run_id"], batch_id, r["consumed"], r["valid"], r["invalid"])
                    for r in counts
                    if r["run_id"]
                ],
            )
            if total:
                log(
                    "batch", query="validate", batch_id=batch_id, input=total, invalid=total - valid
                )
        finally:
            batch.unpersist()

    return (
        staged.writeStream.queryName("validate")
        .foreachBatch(handle)
        .option("checkpointLocation", f"{cfg.checkpoint_dir}/validate")
        .trigger(processingTime=cfg.trigger)
        .start()
    )


def valid_events(spark: SparkSession, cfg: ProcessorConfig) -> DataFrame:
    """Typed, enriched, watermarked and de-duplicated (by event_id) valid events."""
    parsed = (
        _source(spark, cfg, TOPIC_VALID)
        .select(parse(F.col("value").cast("string"), ddl()).alias("e"))
        .select("e.*")
    )
    return prepare(parsed, cfg.watermark)


def aggregation_query(
    spark: SparkSession, cfg: ProcessorConfig, sink: PostgresSink, spec: AggSpec
) -> StreamingQuery:
    def handle(batch: DataFrame, batch_id: int) -> None:
        records = batch.collect()  # aggregate rows: small (windows x keys), not raw events
        rows = to_rows(spec, records, batch_id)
        n = sum(len(v) for v in rows.values())
        sink.write(rows, progress=(spec.name, batch_id, len(records), 0, 0, n))

    return (
        aggregate(valid_events(spark, cfg), spec)
        .writeStream.queryName(spec.name)
        .outputMode("update")
        .foreachBatch(handle)
        .option("checkpointLocation", f"{cfg.checkpoint_dir}/{spec.name}")
        .trigger(processingTime=cfg.trigger)
        .start()
    )


def start_all(spark: SparkSession, cfg: ProcessorConfig) -> list[StreamingQuery]:
    sink = PostgresSink(cfg.dsn)
    queries = [validation_query(spark, cfg, sink)]
    queries += [aggregation_query(spark, cfg, sink, spec) for spec in SPECS]
    return queries
