"""The emitter: one function, four gates, and a contract that makes it safe.

``emit()`` is called from order paths, agent turns and error handlers. Its
contract is what makes that acceptable:

1. **It never raises.** Every failure inside it is swallowed. Telemetry that can
   break the bot is worse than no telemetry, so the whole body is wrapped and
   the fallback is silence.
2. **It never blocks and never does I/O** in the host process. It appends to a
   bounded ``deque``; the network is somebody else's job, 15 minutes later.
3. **It says no more than the level allows.** The first gate is a cached level
   read: ``CONDOR_TELEMETRY=off`` makes it return before it has looked at its
   own arguments, and at the default ``ping`` floor only the four adoption
   events pass the schema gate — everything else needs an explicit ``usage``
   opt-in.
4. **It cannot emit what the schema does not declare.** Properties are
   allowlisted, type-checked and truncated by :mod:`condor.telemetry.schema`.

The rate limiter is the fourth gate and exists for one specific failure: an
error loop turning the ``error`` event into a self-inflicted flood. Overflow
increments ``dropped``, which rides in the envelope — the collector sees an
honest count instead of a suspiciously quiet incident.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque

log = logging.getLogger(__name__)

RING_MAX = 2000
RATE_PER_MIN = 60

_buffer: deque[dict] = deque(maxlen=RING_MAX)
_dropped = 0
_tokens: dict[str, tuple[float, float]] = {}

# False until a process registers a flush job (see condor.telemetry.init). A
# process without one — the out-of-process MCP server — writes straight to its
# own spool file instead, because an in-memory ring there would be lost on exit.
_hosted = False


def set_hosted(value: bool) -> None:
    global _hosted
    _hosted = value


def _take_token(name: str) -> bool:
    """Token bucket, :data:`RATE_PER_MIN` per event name per minute."""
    now = time.monotonic()
    tokens, last = _tokens.get(name, (float(RATE_PER_MIN), now))
    tokens = min(float(RATE_PER_MIN), tokens + (now - last) * RATE_PER_MIN / 60.0)
    if tokens < 1.0:
        _tokens[name] = (tokens, now)
        return False
    _tokens[name] = (tokens - 1.0, now)
    return True


def emit(name: str, /, **props) -> None:
    """Record one event. Synchronous, never raises, never does I/O.

    The event name is positional-only so that an event may legitimately declare
    a property called ``name`` (``command`` does) without colliding with it.

    Unknown event names and undeclared properties are dropped rather than
    reported, so a stale call site degrades to silence instead of an exception
    on a trading path.
    """
    global _dropped
    try:
        from condor.telemetry import consent, schema

        level = consent.level()
        if level == consent.OFF:
            return
        if not schema.allowed_at(name, level):
            return
        if not _take_token(name):
            _dropped += 1
            return
        clean = schema.sanitize(name, props)
        if clean is None:
            return
        event = {
            "id": uuid.uuid4().hex,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "name": name,
            "props": clean,
        }
        if _hosted:
            if len(_buffer) == RING_MAX:
                _dropped += 1  # the deque is about to evict the oldest
            _buffer.append(event)
        else:
            from condor.telemetry import outbox

            outbox.spool(event)
    except Exception:  # noqa: BLE001 - the whole point of this function
        try:
            log.debug("Telemetry emit failed for %r", name, exc_info=True)
        except Exception:
            pass


def buffered() -> int:
    return len(_buffer)


def dropped() -> int:
    return _dropped


def drain() -> tuple[list[dict], int]:
    """Take the ring and the dropped count together, and reset both."""
    global _dropped
    events = list(_buffer)
    _buffer.clear()
    count, _dropped = _dropped, 0
    return events, count


def discard_buffer() -> None:
    """Throw the ring away without sending it. Used when usage consent is
    withdrawn."""
    global _dropped
    _buffer.clear()
    _dropped = 0
    _tokens.clear()


async def flush(reason: str = "job") -> int:
    """Try to deliver everything pending. Returns the number of events sent.

    Returns 0 without reading a file when the operator has forced ``off``. If
    :func:`condor.telemetry.outbox.endpoint` ever yields nothing, the batch is
    stashed rather than sent — the guard that keeps a delivery-less build from
    losing events, and the seam the tests use; a shipped install always has the
    compiled-in collector, so it does not take that path. An unanswered install
    flushes too: the ping floor is only worth anything if the adoption events
    actually leave.
    """
    try:
        from condor.telemetry import consent, context, outbox

        if consent.level() == consent.OFF:
            return 0

        events, dropped_count = drain()
        events.extend(outbox.drain_spools())
        events.extend(outbox.take_stashed())
        if not events:
            return 0

        if not outbox.endpoint():
            outbox.stash(events)
            return 0

        sent = 0
        for batch in outbox.batches(events):
            envelope = context.envelope(batch, dropped_count, consent.level())
            dropped_count = 0
            if await outbox.post(envelope):
                sent += len(batch)
            else:
                outbox.stash(batch)
        return sent
    except Exception:
        log.debug("Telemetry flush failed (%s)", reason, exc_info=True)
        return 0
