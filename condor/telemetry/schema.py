"""THE TAXONOMY — the allowlist that makes the privacy promise a property of code.

Every event Condor can emit is declared here, with every property it is allowed
to carry and the shape that property must have. :func:`sanitize` is the only way
an event reaches a buffer, and it **drops** anything not declared rather than
passing it through. That is deliberate: the "never collected" list in
``PRIVACY.md`` is not a convention the call sites are trusted to honour, it is
what this module structurally cannot let past.

Consequences worth stating out loud, because they are the point:

- An undeclared property is discarded, whatever it is called. A tap that grows a
  ``wallet=`` argument by accident leaks nothing.
- Free-form strings are capped at :data:`MAX_STR` characters, so nothing long
  enough to be a key, an address, a prompt or a URL survives intact.
- Enum properties are snapped to their allowlist or to ``other``, so cardinality
  is bounded by this file rather than by whatever the caller happened to hold.

Deliberately absent, and asserted by ``tests/test_telemetry.py``: amounts,
balances, PnL, trading pairs, order ids, wallet addresses, server names, URLs,
hostnames, IPs, API keys, Telegram ids or usernames, prompts, agent replies,
routine configs, journal text, and exception message strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# One place to change the blast radius of any single value.
MAX_STR = 64
MAX_MAP_KEYS = 20
MAX_LIST = 5

# Free-form strings keep only characters our own identifiers use. It is a second
# fence behind the allowlist: even a declared string property cannot carry a
# sentence, a path with a home directory in it, or a URL with a token in it.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._:/@#-]+")


@dataclass(frozen=True)
class PropSpec:
    """What one property of one event is allowed to be."""

    kind: str  # str | int | float | bool | enum | map | list
    allowed: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.kind == "enum" and not self.allowed:
            raise ValueError("enum PropSpec needs an allowlist")


def _s() -> PropSpec:
    return PropSpec("str")


def _i() -> PropSpec:
    return PropSpec("int")


def _f() -> PropSpec:
    return PropSpec("float")


def _b() -> PropSpec:
    return PropSpec("bool")


def _e(*allowed: str) -> PropSpec:
    return PropSpec("enum", tuple(allowed))


SURFACES = ("telegram", "web", "mcp", "strategy", "other")

# The commands we ship. Anything else a user types is reported as `other`, so a
# typo, a third-party bot's command, or a private fork's addition can never
# widen the value space.
COMMANDS = (
    "start",
    "portfolio",
    "bots",
    "new_bot",
    "trade",
    "swap",
    "lp",
    "routines",
    "executors",
    "agent",
    "stop",
    "delegations",
    "memory",
    "cancel",
    "servers",
    "keys",
    "gateway",
    "admin",
    "update",
    "web",
)

MODULES = (
    "bots",
    "cex",
    "dex",
    "trade",
    "agents",
    "routines",
    "config",
    "portfolio",
    "executors",
    "admin",
    "start",
    "other",
)

# Events an install at level `ping` may send. Everything else needs `usage`.
PING_EVENTS = frozenset({"install", "heartbeat", "version_change", "shutdown"})


EVENTS: dict[str, dict[str, PropSpec]] = {
    # ── Adoption & retention ─────────────────────────────────────────────
    # `install` carries no properties: the envelope context is the payload.
    "install": {},
    "heartbeat": {
        "uptime_h": _f(),
        "active_users_24h": _i(),
        "surfaces": PropSpec("map"),
        # `health` rides here rather than being its own event.
        "hb_api_online": _b(),
        "gateway_online": _b(),
        "hb_api_version": _s(),
    },
    "version_change": {
        # Named `from_version`/`to_version`, not `from`/`to`: `from` is a Python
        # keyword and emit() takes keyword arguments.
        "from_version": _s(),
        "to_version": _s(),
        "was_behind": _i(),
    },
    "shutdown": {
        "reason": _e("signal", "crash", "restart"),
        "uptime_h": _f(),
    },
    # ── Feature usage ────────────────────────────────────────────────────
    "command": {
        "name": _e(*COMMANDS, "other"),
        "surface": _e(*SURFACES),
        "user_hash": _s(),
        "authorized": _b(),
    },
    "action": {
        "module": _e(*MODULES),
        "verb": _s(),
        "surface": _e(*SURFACES),
        "status": _i(),
    },
    "feature_first_use": {"feature": _s()},
    "bot_deploy": {
        "controller_type": _s(),
        "connector": _s(),
        "is_paper": _b(),
    },
    "executor_deploy": {"executor_type": _s(), "connector": _s()},
    "trade": {
        # No trading pair, and no amount. See PRIVACY.md: an install that trades
        # one pair, plus timestamps, is a deanonymizable position disclosure.
        "venue": _e("cex", "dex"),
        "connector": _s(),
        "side": _e("buy", "sell", "other"),
        "order_type": _s(),
    },
    "routine_run": {
        "routine": _s(),
        "kind": _e("oneshot", "continuous", "other"),
        "trigger": _e("manual", "schedule", "background", "web", "mcp", "other"),
        "duration_ms": _i(),
        "ok": _b(),
    },
    # ── Reliability ──────────────────────────────────────────────────────
    "error": {
        "where": _s(),
        "exc_type": _s(),
        # A hash of the message, never the message. Grouping still works; the
        # balance, hostname or key that message might have contained does not
        # survive the hash.
        "sig": _s(),
        "frames": PropSpec("list"),
        "surface": _e(*SURFACES),
        "fatal": _b(),
    },
    "upstream_error": {
        "service": _e("hb_api", "gateway", "llm", "telegram", "other"),
        "op": _s(),
        "status": _s(),
    },
    # ── Agent economics ──────────────────────────────────────────────────
    "agent_turn": {
        "kind": _e("chat", "consult", "delegate", "tick", "other"),
        "provider": _s(),
        "model": _s(),
        "tool_calls": _i(),
        "duration_ms": _i(),
        "outcome": _e("done", "error", "aborted"),
        "tokens_in": _i(),
        "tokens_out": _i(),
        "surface": _e(*SURFACES),
        # `agent_tools` rides here: a bounded map of tool name -> count.
        "tools": PropSpec("map"),
        # Key-shaped runs the ingress scrubber found, as kind -> count. A
        # finding never carries its value, so neither can this.
        "secrets": PropSpec("map"),
    },
    # An MCP tool call observed from the out-of-process server, which cannot see
    # the turn it belongs to (see condor/telemetry/outbox.py). Not in the
    # original design's table; added so the subprocess spool has something
    # declared to carry.
    "mcp_tool": {"tool": _s(), "ok": _b(), "duration_ms": _i()},
    "strategy_run": {
        "mode": _e("dry_run", "run_once", "loop", "other"),
        "frequency_sec": _i(),
        "ticks": _i(),
        "bot_mode": _s(),
        "has_risk_limits": _b(),
        "stopped_by": _s(),
    },
    "confirmation": {
        "tool": _s(),
        "decision": _e("allow", "deny", "timeout"),
    },
}


def is_known(name: str) -> bool:
    return name in EVENTS


def allowed_at(name: str, level: str) -> bool:
    """Is this event permitted at this telemetry level?"""
    if level == "usage":
        return name in EVENTS
    if level == "ping":
        return name in PING_EVENTS
    return False


def clean_str(value: object) -> str | None:
    """Reduce any value to a short, identifier-shaped string, or reject it."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = _SAFE_CHARS.sub("_", str(value)).strip("_")
    if not text:
        return None
    return text[:MAX_STR]


def _coerce(spec: PropSpec, value: object) -> object | None:
    """Force one value into its declared shape, or return None to drop it."""
    if value is None:
        return None

    if spec.kind == "bool":
        return value if isinstance(value, bool) else None

    if spec.kind == "int":
        # bool is an int in Python; a bool in an int slot is a caller bug.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    if spec.kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return round(float(value), 3)

    if spec.kind == "str":
        return clean_str(value)

    if spec.kind == "enum":
        text = clean_str(value)
        if text is None:
            return None
        return text if text in spec.allowed else "other"

    if spec.kind == "list":
        if not isinstance(value, (list, tuple)):
            return None
        out = [s for s in (clean_str(v) for v in value[:MAX_LIST]) if s]
        return out or None

    if spec.kind == "map":
        if not isinstance(value, dict):
            return None
        out: dict[str, int] = {}
        for key, count in list(value.items())[:MAX_MAP_KEYS]:
            safe_key = clean_str(key)
            if safe_key is None:
                continue
            if isinstance(count, bool) or not isinstance(count, (int, float)):
                continue
            out[safe_key] = int(count)
        return out or None

    return None


def sanitize(name: str, props: dict | None) -> dict | None:
    """Return the emittable form of one event, or None if it is not emittable.

    Undeclared properties are dropped silently — the caller is not trusted, and
    a warning that names the offending value would only move the leak into the
    log file.
    """
    spec = EVENTS.get(name)
    if spec is None:
        return None
    if not props:
        return {}

    clean: dict = {}
    for key, value in props.items():
        prop_spec = spec.get(key)
        if prop_spec is None:
            continue
        coerced = _coerce(prop_spec, value)
        if coerced is not None:
            clean[key] = coerced
    return clean
