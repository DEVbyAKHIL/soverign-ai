# syntax=docker/dockerfile:1.7
# spark-stream-processor: Spark 3.5 Structured Streaming (local mode) + the Kafka connector.
FROM apache/spark:3.5.8-scala2.12-java17-python3-ubuntu AS runtime

USER root
# Security patches for the base OS packages at build time (Trivy gates on fixable HIGH/CRITICAL).
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
# Kafka connector jars, pinned and checksum-verified at build time (no runtime --packages download).
ADD --chmod=644 --checksum=sha256:d2b7068eabce2a1267103625e58efae32f7ee384db1cbc22424532ad2ed95fb4 \
    https://repo1.maven.org/maven2/org/apache/spark/spark-sql-kafka-0-10_2.12/3.5.8/spark-sql-kafka-0-10_2.12-3.5.8.jar /opt/spark/jars/
ADD --chmod=644 --checksum=sha256:85f44359b60943d262f37d1327ebb7931fe47e0392286fe3023299e25b84b0d8 \
    https://repo1.maven.org/maven2/org/apache/spark/spark-token-provider-kafka-0-10_2.12/3.5.8/spark-token-provider-kafka-0-10_2.12-3.5.8.jar /opt/spark/jars/
ADD --chmod=644 --checksum=sha256:9d0a9058c8ede79db6e54ae378ed16fe8d1bead2894635a4aa6aa0b7981668c6 \
    https://repo1.maven.org/maven2/org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar /opt/spark/jars/
ADD --chmod=644 --checksum=sha256:ea0505ee7515e58b1ac0e686e4d1a5d9f7d808e251a61bc371aa0595b9963f83 \
    https://repo1.maven.org/maven2/org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar /opt/spark/jars/

COPY requirements-spark.txt /tmp/requirements-spark.txt
RUN python3 -m pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements-spark.txt \
 && rm /tmp/requirements-spark.txt \
 && mkdir -p /checkpoints /tmp/spark \
 && chown 185:185 /checkpoints /tmp/spark \
 && ls /opt/spark/python/lib/py4j-*-src.zip

WORKDIR /app
COPY --chown=root:root events ./events
COPY --chown=root:root schemas ./schemas
COPY --chown=root:root streaming ./streaming

ENV SPARK_HOME=/opt/spark \
    PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-0.10.9.7-src.zip:/app \
    PYSPARK_PYTHON=python3 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    SPARK_LOCAL_DIRS=/tmp/spark \
    JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=/tmp/spark" \
    PYSPARK_SUBMIT_ARGS="--driver-memory 2g --conf spark.driver.extraJavaOptions=-Duser.timezone=UTC pyspark-shell"

USER 185
EXPOSE 9100
HEALTHCHECK --interval=20s --timeout=5s --retries=3 --start-period=90s \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9100/healthz',timeout=4).status==200 else 1)"
CMD ["python3", "-m", "streaming.main"]

# Test stage (compose service spark-tests, profile "test"): the aggregation specs run against static
# DataFrames in the same Spark/Python versions as production. Not used by the runtime service.
FROM runtime AS test
USER root
RUN python3 -m pip install --no-cache-dir --disable-pip-version-check pytest==9.2.0
COPY --chown=root:root producer ./producer
COPY --chown=root:root tests/__init__.py ./tests/__init__.py
COPY --chown=root:root tests/spark ./tests/spark
USER 185
HEALTHCHECK NONE
# --noconftest: tests/conftest.py belongs to the gateway suite (asyncpg, fakeredis, app factory).
CMD ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", "tests/spark"]
