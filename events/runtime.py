"""Process plumbing for the streaming services: health + metrics endpoint, graceful shutdown."""

import asyncio
import contextlib
import random
import signal
import time
from collections.abc import Awaitable, Callable

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

log = structlog.get_logger("streaming")


class Heartbeat:
    """Healthy while the main loop keeps beating; a stuck loop turns /healthz red."""

    def __init__(self, max_age_s: float) -> None:
        self.max_age_s = max_age_s
        self.last = time.monotonic()

    def beat(self) -> None:
        self.last = time.monotonic()

    def healthy(self) -> bool:
        return time.monotonic() - self.last <= self.max_age_s


def _response(status: str, body: bytes, content_type: str) -> bytes:
    head = (
        f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + body


async def _handle(heartbeat: Heartbeat, reader, writer) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        path = line.decode("latin-1").split(" ")[1] if line.count(b" ") >= 2 else ""
        if path == "/healthz":
            ok = heartbeat.healthy()
            writer.write(
                _response(
                    "200 OK" if ok else "503 Service Unavailable",
                    b'{"status":"ok"}' if ok else b'{"status":"stalled"}',
                    "application/json",
                )
            )
        elif path == "/metrics":
            writer.write(_response("200 OK", generate_latest(), CONTENT_TYPE_LATEST))
        else:
            writer.write(_response("404 Not Found", b"", "text/plain"))
        await writer.drain()
    except (TimeoutError, ConnectionError, IndexError):
        pass
    finally:
        writer.close()


async def serve_ops(heartbeat: Heartbeat, port: int) -> asyncio.Server:
    """/healthz and /metrics on an internal port (not published to the host)."""
    return await asyncio.start_server(
        lambda r, w: _handle(heartbeat, r, w),
        host="0.0.0.0",  # noqa: S104  # nosec B104 - container-internal port, not published
        port=port,
    )


def stop_event() -> asyncio.Event:
    """Set on SIGTERM/SIGINT so loops can finish the current batch and exit cleanly."""
    ev = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # Windows dev hosts
            loop.add_signal_handler(sig, ev.set)
    return ev


async def start_with_retry(
    start: Callable[[], Awaitable[None]],
    stop: asyncio.Event,
    beat: Callable[[], None],
    max_delay_s: float = 30.0,
) -> bool:
    """Kafka may still be starting: retry the connection with exponential backoff + jitter.
    Returns False if asked to stop before a connection was made."""
    attempt = 0
    while not stop.is_set():
        beat()
        try:
            await start()
            return True
        except Exception as exc:
            attempt += 1
            wait = min(max_delay_s, 2 ** (attempt - 1)) * (1 - 0.2 * random.random())  # noqa: S311  # nosec B311
            log.warning("kafka_connect_retry", error=type(exc).__name__, retry_in_s=round(wait, 2))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), wait)
    return False
