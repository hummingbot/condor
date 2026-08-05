"""Bridge an SSE event stream into an asyncio.Queue.

Consumers that already loop over a queue (the Telegram streamer, the loop
engine) keep working unchanged when the runtime moves out of process: only the
producer behind the queue changes.

Unused while ``CONDOR_RUNTIME_MODE=local`` — it exists so that FEAT-014 is a
switch in ``client.prompt`` rather than a rewrite of every consumer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from condor.runtime.events import EventType, RuntimeEvent

log = logging.getLogger(__name__)

# Pushed to wake a blocked consumer immediately on cancellation.
SENTINEL: RuntimeEvent | None = None


class SSEToQueueAdapter:
    """Drain a RuntimeEvent iterator into a queue, tracking the terminal event."""

    def __init__(self, maxsize: int = 0):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.done_event: RuntimeEvent | None = None
        self._cancelled = False

    async def pump(self, events: AsyncIterator[RuntimeEvent]) -> None:
        """Consume ``events`` until exhausted, cancelled, or a DONE arrives.

        Heartbeats are passed through rather than filtered: consumers use them
        to reset inactivity timers, so swallowing them here would make a slow
        agent look like a hung one.
        """
        try:
            async for event in events:
                if self._cancelled:
                    break
                if event.type == EventType.DONE:
                    self.done_event = event
                await self.queue.put(event)
                if event.type == EventType.DONE:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - forwarded to the consumer
            log.exception("SSE pump failed")
            await self.queue.put(RuntimeEvent.error(str(exc)))
        finally:
            # Always unblock the consumer, even on an abrupt end of stream.
            await self.queue.put(SENTINEL)

    def signal_cancel(self) -> None:
        """Stop pumping and wake a consumer blocked on ``queue.get()``."""
        self._cancelled = True
        try:
            self.queue.put_nowait(SENTINEL)
        except asyncio.QueueFull:  # pragma: no cover - only with a bounded queue
            log.debug("Queue full while signalling cancel")

    async def events(self) -> AsyncIterator[RuntimeEvent]:
        """Iterate queued events until the sentinel."""
        while True:
            event = await self.queue.get()
            if event is SENTINEL:
                return
            yield event
