"""``POST /v1/events`` — the one public, unauthenticated door.

Installs are anonymous by design, so there is no credential to check: anything
on the internet can POST here. The pipeline below is ordered so that everything
cheap happens before anything expensive, and so that a hostile body is refused
before it can cost a parse, a memory allocation, or a database round trip.

1. **Body cap.** 1 MB, enforced while streaming. An oversized body is dropped
   without ever being fully buffered or parsed.
2. **Rate limit by source IP.** Token bucket, before the JSON parser runs.
3. **Parse and validate the envelope.** Unknown ``schema`` versions and more
   than :data:`MAX_EVENTS` events are refused whole.
4. **Rate limit by ``install_id``**, now that we know it — still before any
   database contact.
5. **Validate each event** against :mod:`condor.telemetry.schema`, the same
   module the client sanitises with. Offenders are dropped and counted; the
   rest of the batch is still accepted, because rejecting a whole envelope over
   one malformed event would let a single client bug erase a day of otherwise
   good data.
6. **Persist** in one transaction, with ``ON CONFLICT DO NOTHING``.

Two rules hold everywhere in this module. Nothing from a payload is ever
formatted into SQL — :mod:`telemetry_server.store` takes parameters only. And
no error response ever echoes any part of the request: the replies are fixed
strings, so this endpoint cannot be turned into a reflector or used to confirm
what the server did with a probe.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from condor.telemetry import schema

log = logging.getLogger(__name__)

router = APIRouter()

KNOWN_SCHEMAS = frozenset({1})
LEVELS = frozenset({"ping", "usage"})

MAX_BODY_BYTES = 1024 * 1024
MAX_EVENTS = 500
MAX_PROPS = 64
MAX_PROVIDERS = 10
MAX_COUNT = 1_000_000
MAX_DROPPED = 10_000_000
# Client clocks on self-hosted boxes drift and occasionally lie. Anything
# further out than this is the clock, not the event.
CLOCK_SLACK = timedelta(hours=48)

RATE_PER_HOUR = 60


class RateLimiter:
    """Token bucket per key, with a bounded key space.

    The bound matters as much as the rate. ``install_id`` is attacker-chosen, so
    a limiter that kept one bucket per id it has ever seen would be a memory
    exhaustion primitive wearing a safety hat. Keys are held in an LRU capped at
    :data:`max_keys`; evicting the least recently used one costs an attacker a
    fresh bucket, which is exactly what they would have had anyway.
    """

    def __init__(self, per_hour: int = RATE_PER_HOUR, max_keys: int = 20_000) -> None:
        self.per_hour = float(per_hour)
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.pop(key, (self.per_hour, now))
        tokens = min(self.per_hour, tokens + (now - last) * self.per_hour / 3600.0)
        allowed = tokens >= 1.0
        self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)
        return allowed

    def reset(self) -> None:
        self._buckets.clear()


by_ip = RateLimiter()
by_install = RateLimiter()


class Refused(Exception):
    """A whole envelope is unusable. Carries a fixed, contentless reason."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _refuse(status: int, reason: str) -> JSONResponse:
    """Every failure reply is a constant. No request data crosses back out."""
    return JSONResponse(status_code=status, content={"error": reason})


async def read_body(request: Request) -> bytes:
    """Read at most :data:`MAX_BODY_BYTES`, refusing anything larger.

    The declared ``Content-Length`` is checked first because it is free, but it
    is not trusted: a chunked request can omit or understate it, so the stream
    is counted as it arrives and abandoned the moment it crosses the cap.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise Refused(413, "body_too_large")
        except ValueError:
            raise Refused(400, "bad_request") from None

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise Refused(413, "body_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def client_ip(request: Request) -> str:
    """The peer address, or the forwarded one only where a proxy is trusted.

    ``X-Forwarded-For`` is a client-supplied header. Honouring it by default
    would hand every caller a free rate-limit bypass, so it is read only when
    the operator has said there is a proxy in front (``TELEMETRY_TRUSTED_PROXY``).
    """
    from telemetry_server.config import trust_proxy

    if trust_proxy():
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    return request.client.host if request.client else "unknown"


def _uuid_or_none(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 45:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _ts_or_none(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clamp_ts(ts: datetime, received_at: datetime) -> datetime:
    """Pull a lying clock back inside the window we are willing to believe."""
    return max(received_at - CLOCK_SLACK, min(ts, received_at + CLOCK_SLACK))


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(MAX_COUNT, value))


def _install_row(envelope: dict, install_id: str, received_at: datetime) -> dict:
    """Flatten ``app`` and ``config`` into the columns we keep.

    Only declared keys are read. An unknown field in either sub-object is not
    stored, not logged, and not reflected — it simply does not exist here.
    """
    app = envelope.get("app")
    app = app if isinstance(app, dict) else {}
    config = envelope.get("config")
    config = config if isinstance(config, dict) else {}

    providers = config.get("llm_providers")
    if isinstance(providers, (list, tuple)):
        clean = [schema.clean_str(p) for p in list(providers)[:MAX_PROVIDERS]]
        providers = [p for p in clean if p]
    else:
        providers = []

    return {
        "install_id": install_id,
        "received_at": received_at,
        "level": envelope["level"],
        "version": schema.clean_str(app.get("version")),
        "branch": schema.clean_str(app.get("branch")),
        "os": schema.clean_str(app.get("os")),
        "arch": schema.clean_str(app.get("arch")),
        "python": schema.clean_str(app.get("python")),
        "in_docker": _flag(app.get("in_docker")),
        "has_hb_api": _flag(config.get("has_hb_api")),
        "has_gateway": _flag(config.get("has_gateway")),
        "has_web": _flag(config.get("has_web")),
        "user_count": _count(config.get("user_count")),
        "server_count": _count(config.get("server_count")),
        "agent_count": _count(config.get("agent_count")),
        "llm_providers": providers,
    }


def validate(envelope: object, received_at: datetime) -> tuple[dict, list[dict], int]:
    """Turn an untrusted body into rows, or raise :class:`Refused`.

    Returns ``(install, events, rejected)``. ``rejected`` counts every offender
    dropped — a whole event whose name we do not know, and each individual
    property that fell outside its declared spec. Both are the early warning
    that a taxonomy change broke something, so they are counted together and
    reported back rather than swallowed.
    """
    if not isinstance(envelope, dict):
        raise Refused(400, "invalid_envelope")
    if envelope.get("schema") not in KNOWN_SCHEMAS:
        raise Refused(400, "unknown_schema")

    install_id = _uuid_or_none(envelope.get("install_id"))
    if install_id is None:
        raise Refused(400, "invalid_envelope")
    if envelope.get("level") not in LEVELS:
        raise Refused(400, "invalid_envelope")

    raw_events = envelope.get("events")
    if not isinstance(raw_events, list):
        raise Refused(400, "invalid_envelope")
    if len(raw_events) > MAX_EVENTS:
        raise Refused(413, "too_many_events")

    install = _install_row(envelope, install_id, received_at)

    events: list[dict] = []
    rejected = 0
    seen: set[str] = set()
    for raw in raw_events:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        event_id = _uuid_or_none(raw.get("id"))
        ts = _ts_or_none(raw.get("ts"))
        name = raw.get("name")
        props = raw.get("props")
        if props is None:
            props = {}
        if (
            event_id is None
            or ts is None
            or not isinstance(name, str)
            or not schema.is_known(name)
            or not isinstance(props, dict)
            or len(props) > MAX_PROPS
        ):
            rejected += 1
            continue
        if event_id in seen:
            # A duplicate inside one envelope: the database would swallow it,
            # but counting it here keeps `accepted` equal to rows written.
            rejected += 1
            continue
        seen.add(event_id)

        clean = schema.sanitize(name, props)
        if clean is None:
            rejected += 1
            continue
        rejected += len(props) - len(clean)

        events.append(
            {
                "id": event_id,
                "install_id": install_id,
                "ts": clamp_ts(ts, received_at),
                "received_at": received_at,
                "name": name,
                "props": clean,
            }
        )

    return install, events, rejected


def _dropped(envelope: dict) -> int:
    value = envelope.get("dropped")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(MAX_DROPPED, value))


@router.post("/v1/events")
async def ingest(request: Request) -> JSONResponse:
    try:
        body = await read_body(request)
    except Refused as refusal:
        return _refuse(refusal.status, refusal.reason)

    if not by_ip.allow(client_ip(request)):
        return _refuse(429, "rate_limited")

    try:
        envelope = json.loads(body)
    except ValueError:
        return _refuse(400, "invalid_json")

    try:
        install, events, rejected = validate(envelope, _now())
    except Refused as refusal:
        return _refuse(refusal.status, refusal.reason)

    if not by_install.allow(install["install_id"]):
        return _refuse(429, "rate_limited")

    store = request.app.state.store
    try:
        stored, duplicates = await store.record(install, events, _dropped(envelope))
    except Exception:
        # The reason a write failed is ours to read, never the caller's.
        log.exception("Telemetry ingest could not persist an envelope")
        return _refuse(503, "unavailable")

    return JSONResponse(
        status_code=202,
        content={"accepted": stored, "rejected": rejected, "duplicates": duplicates},
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
