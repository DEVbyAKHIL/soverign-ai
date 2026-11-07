"""Entry point: python3 -m streaming.main (inside infra/docker/spark.Dockerfile)."""

import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pyspark.sql import SparkSession

from streaming.config import ProcessorConfig
from streaming.jobs import start_all
from streaming.log import log

INPUT_RATE = Gauge("stream_input_rows_per_second", "Input rows/s (last batch)", ["query"])
PROCESS_RATE = Gauge("stream_processed_rows_per_second", "Processed rows/s", ["query"])
BATCH_ROWS = Gauge("stream_batch_input_rows", "Rows in the last micro-batch", ["query"])
BATCH_ID = Gauge("stream_batch_id", "Last completed micro-batch id", ["query"])
BATCH_MS = Gauge("stream_batch_duration_ms", "Duration of the last micro-batch", ["query"])
STATE_ROWS = Gauge("stream_state_rows", "Rows held in the state store", ["query"])
LATE_ROWS = Gauge(
    "stream_rows_dropped_by_watermark", "Late rows dropped in the last micro-batch", ["query"]
)
ACTIVE = Gauge("stream_query_active", "1 while the query runs", ["query"])


def session(cfg: ProcessorConfig) -> SparkSession:
    return (
        SparkSession.builder.appName("governance-stream-processor")
        .master(cfg.master)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", str(cfg.shuffle_partitions))
        .config(
            "spark.sql.streaming.stateStore.providerClass",
            "org.apache.spark.sql.execution.streaming.state.RocksDBStateStoreProvider",
        )
        .config("spark.sql.streaming.stateStore.rocksdb.changelogCheckpointing.enabled", "true")
        .config("spark.scheduler.mode", "FAIR")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )


def refresh_metrics(queries) -> bool:
    healthy = True
    for q in queries:
        name = q.name or q.id
        active = q.isActive and q.exception() is None
        healthy &= active
        ACTIVE.labels(name).set(1 if active else 0)
        p = q.lastProgress or {}
        INPUT_RATE.labels(name).set(p.get("inputRowsPerSecond") or 0)
        PROCESS_RATE.labels(name).set(p.get("processedRowsPerSecond") or 0)
        BATCH_ROWS.labels(name).set(p.get("numInputRows") or 0)
        BATCH_ID.labels(name).set(p.get("batchId", -1))
        BATCH_MS.labels(name).set((p.get("durationMs") or {}).get("triggerExecution", 0))
        ops = p.get("stateOperators", [])
        STATE_ROWS.labels(name).set(sum(s.get("numRowsTotal", 0) for s in ops))
        LATE_ROWS.labels(name).set(sum(s.get("numRowsDroppedByWatermark", 0) for s in ops))
    return healthy


def ops_server(queries, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            healthy = refresh_metrics(queries)
            if self.path == "/healthz":
                code, body, ctype = (200 if healthy else 503), b"{}", "application/json"
            elif self.path == "/metrics":
                code, body, ctype = 200, generate_latest(), CONTENT_TYPE_LATEST
            else:
                code, body, ctype = 404, b"", "text/plain"
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # silence per-request access logs
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104  # nosec B104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    cfg = ProcessorConfig.from_env()
    if not cfg.dsn:
        log("missing_config", level="error", setting="PROCESSOR_DSN")
        return 2
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    spark = session(cfg)
    spark.sparkContext.setLogLevel("WARN")
    queries = start_all(spark, cfg)
    server = ops_server(queries, cfg.ops_port)
    log("processor_started", queries=[q.name for q in queries], watermark=cfg.watermark)
    code = 0
    while not stop.wait(5):
        failed = [q.name for q in queries if not q.isActive or q.exception() is not None]
        if failed:
            # Fail fast: the container restarts and every query resumes from its checkpoint.
            log("query_failed", level="error", queries=failed)
            code = 1
            break
    log("processor_stopping", reason="signal" if code == 0 else "query_failed")
    for q in queries:
        try:
            q.stop()  # finishes cleanly; committed offsets and state stay in the checkpoint
        except Exception as exc:  # noqa: BLE001 - keep stopping the rest
            log("query_stop_error", level="warning", query=q.name, error=type(exc).__name__)
    server.shutdown()
    spark.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
