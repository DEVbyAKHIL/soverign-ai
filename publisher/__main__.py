"""Entry point: python -m publisher"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from events.runtime import Heartbeat, serve_ops, start_with_retry, stop_event
from gateway.logging_config import configure_logging, get_logger
from publisher.config import PublisherSettings
from publisher.sender import KafkaSender
from publisher.service import run_loop

log = get_logger("event-publisher")


async def main() -> None:
    cfg = PublisherSettings()
    configure_logging(cfg.log_level)
    stop = stop_event()
    heartbeat = Heartbeat(max_age_s=max(60.0, cfg.publisher_send_timeout_s * 2))
    ops = await serve_ops(heartbeat, cfg.publisher_ops_port)
    engine = create_async_engine(cfg.database_url, pool_pre_ping=True, pool_size=2)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sender = KafkaSender(cfg.kafka_bootstrap, cfg.publisher_send_timeout_s)
    try:
        if not await start_with_retry(sender.start, stop, heartbeat.beat):
            return
        log.info("event_publisher_started", batch_size=cfg.publisher_batch_size)
        await run_loop(
            sessions,
            sender,
            stop,
            batch_size=cfg.publisher_batch_size,
            poll_interval_s=cfg.publisher_poll_interval_s,
            backoff_base_s=cfg.publisher_backoff_base_s,
            backoff_max_s=cfg.publisher_backoff_max_s,
            beat=heartbeat.beat,
        )
    finally:
        log.info("event_publisher_stopping")
        await sender.stop()
        await engine.dispose()
        ops.close()


if __name__ == "__main__":
    asyncio.run(main())
