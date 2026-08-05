"""Server-sent-events framing for runtime event streams.

SSE is the transport for prompts because a prompt is exactly a
request/response-stream: no reconnect protocol, no correlation ids, and it
survives proxies that mangle WebSockets. The framing lives here rather than in
the route so the HTTP client added in FEAT-014 can reuse the parser.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from condor.runtime.events import EventType, RuntimeEvent
from condor.runtime.timeouts import TIMEOUTS

# Emitted so proxies and clients see traffic on a quiet stream. A comment frame
# is ignored by every SSE parser, including EventSource.
KEEPALIVE_FRAME = ": keepalive\n\n"

# Cadence of the keepalive above, from the shared timeout policy.
HEARTBEAT_INTERVAL = TIMEOUTS.sse_heartbeat

# Headers an SSE response must carry. X-Accel-Buffering disables nginx's
# response buffering, which otherwise holds frames until the stream closes and
# makes streaming look like a single slow response.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def format_event(event: RuntimeEvent) -> str:
    """Render one event as an SSE frame."""
    payload = json.dumps(event.to_wire(), default=str)
    return f"event: {event.type.value}\ndata: {payload}\n\n"


async def event_stream(events: AsyncIterator[RuntimeEvent]) -> AsyncIterator[str]:
    """Frame an event iterator, guaranteeing a terminal ``done``.

    A client waiting for ``done`` must never hang because the producer raised,
    so a failure is converted into an ``error`` frame plus ``done``.
    """
    saw_done = False
    try:
        async for event in events:
            if event.type == EventType.DONE:
                saw_done = True
            yield format_event(event)
    except Exception as exc:  # noqa: BLE001 - reported to the client as a frame
        yield format_event(RuntimeEvent.error(str(exc)))
        saw_done = False
    finally:
        if not saw_done:
            yield format_event(RuntimeEvent.done("disconnected"))


def parse_frame(raw: str) -> RuntimeEvent | None:
    """Parse one SSE frame back into an event, or None for comments/keepalives."""
    data_lines = [
        line[len("data:") :].strip()
        for line in raw.splitlines()
        if line.startswith("data:")
    ]
    if not data_lines:
        return None
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "type" not in payload:
        return None
    return RuntimeEvent(**payload)
