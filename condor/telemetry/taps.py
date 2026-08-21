"""The taps: the only code that turns something happening into an event.

Call sites stay one line long and stay dumb. Everything that decides *what* is
allowed to be said lives here and in :mod:`condor.telemetry.schema`, so the
privacy review has two files to read rather than fifteen.

Every public function here is defensive to the point of paranoia: a tap runs
inside a command handler, an error handler, an order path and a tick loop, and
a tap that raises would turn "we learned nothing" into "the bot broke". They all
swallow, and :func:`condor.telemetry.emitter.emit` swallows again underneath.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
import time
import traceback
from pathlib import Path

from condor.telemetry.emitter import emit

log = logging.getLogger(__name__)

TELEMETRY_FLUSH_JOB = "telemetry_flush"
TELEMETRY_HEARTBEAT_JOB = "telemetry_heartbeat"
FLUSH_INTERVAL_S = 15 * 60
HEARTBEAT_INTERVAL_S = 6 * 3600

_REPO = Path(__file__).resolve().parent.parent.parent
# Frames from our own packages only. A frame in site-packages tells us nothing
# actionable and its path can carry a username.
_OWN_PACKAGES = ("condor", "handlers", "utils", "mcp_servers", "routines")

# Rolling, in-memory only, never persisted and never transmitted as a set —
# only its cardinality reaches an envelope.
_seen_users: dict[str, float] = {}
_surface_counts: dict[str, int] = {}

# A leading run of lowercase and underscores. Deliberately strict: callback data
# like ``admin:approve_12345`` or ``bots:view_MyBotName`` carries an identifier
# after the verb, and cutting at the first digit or capital drops it.
_VERB = re.compile(r"^[a-z][a-z_]{0,31}")


def _quiet(fn):
    """A tap must never be the reason a handler failed."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.debug("Telemetry tap %s failed", fn.__name__, exc_info=True)
            return None

    return wrapper


def _on() -> bool:
    from condor.telemetry import consent

    return consent.level() != consent.OFF


# ── Identity ─────────────────────────────────────────────────────────────


@_quiet
def user_hash(user_id) -> str | None:
    """A per-install pseudonym for one Telegram user.

    Salted with the install's own secret, which never leaves the machine, so the
    result counts distinct users within an install and is useless for correlating
    the same person across two installs. The Telegram id itself is never sent.
    """
    from condor.telemetry import consent

    secret = consent.install_secret()
    if not secret or user_id is None:
        return None
    digest = hashlib.sha256(f"{secret}:{user_id}".encode()).hexdigest()
    return digest[:16]


def _note_user(hashed: str | None, surface: str) -> None:
    if hashed:
        _seen_users[hashed] = time.time()
    _surface_counts[surface] = _surface_counts.get(surface, 0) + 1


@_quiet
def feature_first_use(feature: str) -> None:
    """Fire once per install, ever. The activation funnel."""
    from condor.telemetry import consent

    # Gated on `usage`, not merely "on": at the ping floor the event could
    # never be sent, and the features_seen list should not accumulate for it.
    if consent.level() != consent.USAGE:
        return
    if consent.mark_feature_seen(feature):
        emit("feature_first_use", feature=feature)


def _verb_of(segment: str) -> str:
    match = _VERB.match((segment or "").strip())
    return (match.group(0).rstrip("_") if match else "") or "other"


# ── Telegram ─────────────────────────────────────────────────────────────


async def telegram_tap(update, context) -> None:
    """Observe every update, in ``group=-1``, without ever deciding anything.

    Registered before the real handlers so it sees commands and callbacks from
    unauthorized users too — who is knocking is signal. It reads authorization
    state; it must never call into ``@restricted``, or a read would become an
    access-control side effect.
    """
    try:
        if not _on():
            return
        user = getattr(update, "effective_user", None)
        hashed = user_hash(getattr(user, "id", None)) if user else None
        authorized = _is_authorized(getattr(user, "id", None))

        message = getattr(update, "message", None)
        text = (getattr(message, "text", None) or "") if message else ""
        if text.startswith("/"):
            raw = text[1:].split(maxsplit=1)[0].split("@")[0].lower()
            _note_user(hashed, "telegram")
            emit(
                "command",
                name=raw,
                surface="telegram",
                user_hash=hashed,
                authorized=authorized,
            )
            if authorized:
                feature_first_use(f"command:{_verb_of(raw)}")
            return

        query = getattr(update, "callback_query", None)
        data = (getattr(query, "data", None) or "") if query else ""
        if data:
            module, _, rest = data.partition(":")
            _note_user(hashed, "telegram")
            emit(
                "action",
                module=module.lower(),
                verb=_verb_of(rest.split(":")[0]),
                surface="telegram",
            )
    except Exception:  # noqa: BLE001
        log.debug("Telegram telemetry tap failed", exc_info=True)


@_quiet
def _is_authorized(user_id) -> bool:
    from condor.telemetry.consent import _cm

    cm = _cm()
    return bool(cm and user_id is not None and cm.is_approved(int(user_id)))


# ── Errors ───────────────────────────────────────────────────────────────


def _frames(exc: BaseException) -> list[str]:
    """Up to five ``file:line`` pairs from our own packages, repo-relative.

    Absolute paths are not sent: they contain the operator's home directory and
    often their username.
    """
    out: list[str] = []
    for frame in traceback.extract_tb(exc.__traceback__):
        try:
            path = Path(frame.filename).resolve().relative_to(_REPO)
        except (ValueError, OSError):
            continue
        if path.parts and path.parts[0] in _OWN_PACKAGES or path.name == "main.py":
            out.append(f"{path.as_posix()}:{frame.lineno}")
    return out[-5:]


@_quiet
def on_error(
    exc: BaseException | None, where: str, surface: str = "other", fatal: bool = False
) -> None:
    """Report that something broke — the shape of it, never its words.

    Only the exception *type*, a hash of its message, and our own stack frames.
    Message strings are where balances, hostnames, URLs and keys leak, so they
    are hashed and discarded: grouping still works, disclosure does not.
    """
    if exc is None or not _on():
        return
    signature = hashlib.sha256(str(exc).encode("utf-8", "replace")).hexdigest()[:12]
    emit(
        "error",
        where=where,
        exc_type=type(exc).__name__,
        sig=signature,
        frames=_frames(exc),
        surface=surface,
        fatal=fatal,
    )


@_quiet
def on_upstream_error(service: str, op: str, status) -> None:
    """A dependency answered badly: which one, which operation, which code."""
    emit("upstream_error", service=service, op=op, status=str(status))


# ── Web ──────────────────────────────────────────────────────────────────


def _route_action(request) -> tuple[str, str] | None:
    """Turn a matched route *template* into (module, verb).

    The template, never the URL: ``/api/v1/bots/{name}`` rather than
    ``/api/v1/bots/my-arb-bot``. Cardinality is bounded by our own router, and
    no path parameter — a bot name, a server name, a report id — is ever read.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return None
    parts = [p for p in template.strip("/").split("/") if p]
    if parts[:2] == ["api", "v1"]:
        parts = parts[2:]
    if not parts:
        return None
    module = parts[0].lower()
    static = [p for p in parts[1:] if not p.startswith("{")]
    verb = _verb_of("_".join(static)) if static else request.method.lower()
    return module, verb


async def web_tap(request, call_next):
    """ASGI middleware: one ``action`` per API call, with its status code.

    Deliberately not wrapped in a swallow-everything decorator: ``call_next``
    must be allowed to raise, or an unhandled route exception would surface as
    "no response returned" instead of the 500 it is. Only the observation is
    guarded.
    """
    response = await call_next(request)
    try:
        if _on() and str(request.url.path).startswith("/api/"):
            action = _route_action(request)
            if action:
                module, verb = action
                _surface_counts["web"] = _surface_counts.get("web", 0) + 1
                emit(
                    "action",
                    module=module,
                    verb=verb,
                    surface="web",
                    status=response.status_code,
                )
    except Exception:  # noqa: BLE001
        log.debug("Web telemetry tap failed", exc_info=True)
    return response


# ── Agents, strategies, routines, confirmations ──────────────────────────


@_quiet
def agent_turn(
    *,
    kind: str,
    provider: str = "",
    model: str = "",
    tool_calls: int = 0,
    duration_ms: int = 0,
    outcome: str = "done",
    surface: str = "other",
    tools: dict | None = None,
) -> None:
    """One turn through ``condor.runtime.client.prompt`` — the single funnel
    Telegram, the dashboard and MCP all cross."""
    _surface_counts[surface] = _surface_counts.get(surface, 0) + 1
    emit(
        "agent_turn",
        kind=kind,
        provider=provider,
        model=model,
        tool_calls=tool_calls,
        duration_ms=duration_ms,
        outcome=outcome,
        surface=surface,
        tools=tools,
    )


@_quiet
def strategy_run(config: dict | None, *, ticks: int = 0, stopped_by: str = "") -> None:
    """A tick-engine session ended. Shape of the run only — never its journal."""
    config = config or {}
    emit(
        "strategy_run",
        mode=config.get("execution_mode", "loop"),
        frequency_sec=config.get("frequency_sec"),
        ticks=ticks,
        bot_mode=config.get("bot_mode"),
        has_risk_limits=bool(config.get("risk_limits")),
        stopped_by=stopped_by,
    )


@_quiet
def routine_run(
    *, routine: str, kind: str, trigger: str, duration_ms: int, ok: bool
) -> None:
    """A routine ran. ``routine`` is the shipped name, or ``custom`` when the
    file is not one of ours — a user's private routine name is theirs."""
    emit(
        "routine_run",
        routine=routine,
        kind=kind,
        trigger=trigger,
        duration_ms=duration_ms,
        ok=ok,
    )


def shipped_routine_name(name: str) -> str:
    """``name`` if the routine ships in this repo, else ``custom``."""
    try:
        if (_REPO / "routines" / f"{name}.py").exists():
            return name
    except Exception:  # noqa: BLE001
        pass
    return "custom"


@_quiet
def confirmation(tool: str, decision: str) -> None:
    emit("confirmation", tool=tool, decision=decision)


@_quiet
def version_change(from_version: str, to_version: str, was_behind: int = 0) -> None:
    emit(
        "version_change",
        from_version=from_version,
        to_version=to_version,
        was_behind=was_behind,
    )


def tracked(tool_name: str):
    """Decorator for an MCP tool, which runs in its own process.

    That process has no scheduler of its own and no shared heap, so the event goes
    straight to its own ``spool.<pid>.jsonl`` and the host drains it later. Only
    the tool name, whether it worked, and how long it took — never arguments,
    never results.
    """

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            started = time.monotonic()
            ok = True
            try:
                return await fn(*args, **kwargs)
            except Exception:
                ok = False
                raise
            finally:
                emit(
                    "mcp_tool",
                    tool=tool_name,
                    ok=ok,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

        return wrapper

    return decorator


# ── Jobs ─────────────────────────────────────────────────────────────────


async def flush_job(context) -> None:
    """The only place in the process that may talk to a collector."""
    from condor.telemetry import emitter

    await emitter.flush("job")


async def heartbeat_job(context) -> None:
    """Proof of life from an install that did nothing else this cycle."""
    try:
        if not _on():
            return
        from condor.telemetry import context as ctx

        cutoff = time.time() - 24 * 3600
        for hashed, seen in list(_seen_users.items()):
            if seen < cutoff:
                _seen_users.pop(hashed, None)
        surfaces = dict(_surface_counts)
        _surface_counts.clear()
        emit(
            "heartbeat",
            uptime_h=ctx.uptime_h(),
            active_users_24h=len(_seen_users),
            surfaces=surfaces,
        )
    except Exception:  # noqa: BLE001
        log.debug("Heartbeat tap failed", exc_info=True)


def register_jobs() -> None:
    """Register the flush and heartbeat jobs, the house pattern from
    ``handlers.admin.update.schedule_update_checks``.

    Registered unconditionally: the jobs are cheap, and both return immediately
    when consent is absent. That keeps a mid-run opt-in working without a
    restart.
    """
    try:
        from condor.scheduler import get_scheduler

        scheduler = get_scheduler()
        for name in (TELEMETRY_FLUSH_JOB, TELEMETRY_HEARTBEAT_JOB):
            scheduler.remove_by_name(name)
        scheduler.run_repeating(
            flush_job, interval=FLUSH_INTERVAL_S, first=60, name=TELEMETRY_FLUSH_JOB
        )
        scheduler.run_repeating(
            heartbeat_job,
            interval=HEARTBEAT_INTERVAL_S,
            first=120,
            name=TELEMETRY_HEARTBEAT_JOB,
        )
    except Exception:  # noqa: BLE001
        log.debug("Could not register telemetry jobs", exc_info=True)
