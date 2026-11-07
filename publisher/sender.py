"""Kafka side of the publisher. A message counts as delivered only when its future resolves,
i.e. the broker acknowledged it (acks=all, idempotent producer)."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from aiokafka import AIOKafkaProducer


@dataclass(frozen=True)
class Message:
    topic: str
    key: bytes
    value: bytes
    headers: tuple[tuple[str, bytes], ...] = ()


class Sender(Protocol):
    async def send_many(self, messages: list[Message]) -> list[BaseException | None]: ...


class KafkaSender:
    def __init__(self, bootstrap: str, timeout_s: float = 30.0, client_id: str = "event-publisher"):
        self.timeout_s = timeout_s
        self.producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap,
            acks="all",
            enable_idempotence=True,
            compression_type="gzip",  # stdlib codec; JSON events compress ~5x on disk
            linger_ms=10,
            request_timeout_ms=int(timeout_s * 1000),
            client_id=client_id,
        )

    async def start(self) -> None:
        await self.producer.start()

    async def stop(self) -> None:
        await self.producer.stop()  # flushes in-flight messages

    async def send_many(self, messages: list[Message]) -> list[BaseException | None]:
        """One result per message: None = acknowledged, otherwise the delivery error."""
        loop = asyncio.get_running_loop()
        futures: list[asyncio.Future] = []
        for m in messages:
            try:
                futures.append(
                    await self.producer.send(m.topic, m.value, key=m.key, headers=list(m.headers))
                )
            except Exception as exc:  # buffer full, producer closed: fail just this message
                fut = loop.create_future()
                fut.set_exception(exc)
                futures.append(fut)
        try:
            done = await asyncio.wait_for(
                asyncio.gather(*futures, return_exceptions=True), self.timeout_s
            )
        except TimeoutError as exc:
            return [exc] * len(messages)
        return [r if isinstance(r, BaseException) else None for r in done]
