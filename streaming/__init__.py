"""spark-stream-processor: validation, enrichment, windowed aggregation, PostgreSQL upserts.

Modules without a pyspark import (config, validation, fields, sink) run and are tested on any host;
the Spark-dependent modules (aggregations, jobs, main) run in infra/docker/spark.Dockerfile.
Code here must stay Python 3.10 compatible (the Spark image's interpreter).
"""
