"""Consent: the machine that decides how much an install says.

The install — not the individual user — is the unit of consent, because the
admin owns the install. The states are ``unknown`` (the default on a fresh
clone), ``granted`` and ``denied`` (legacy — older installs could refuse from
the prompt), stored in ``config.yml`` under ``telemetry`` alongside the
install's identity.

Two rules matter more than the rest:

**The floor is ``ping``.** Every install is counted. An install whose admin has
never answered the prompt emits the four adoption events (``install``,
``heartbeat``, ``version_change``, ``shutdown``) and nothing else; the prompt
decides one thing only — whether the ``usage`` events are added on top. There
is no "off" answer on the form.

**The environment wins.** ``CONDOR_TELEMETRY`` in ``utils/config.py`` overrides
the stored answer in both directions, and it is the only way to reach level
``off`` — the operator's kill switch for a headless or containerized install.

Reads never create ``config.yml``; the identity ids are materialized by
:func:`condor.telemetry.init` on the first boot that can actually emit.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

OFF = "off"
PING = "ping"
USAGE = "usage"
LEVELS = (OFF, PING, USAGE)

UNKNOWN = "unknown"
GRANTED = "granted"
DENIED = "denied"

# Answer -> level, the two buttons of the admin prompt. "off" is deliberately
# not an answer: install counting is the floor, and only the environment can
# silence it entirely.
ANSWER_LEVELS = {"usage": USAGE, "ping": PING}

_cached_level: str | None = None


def _cm():
    """The ConfigManager, but only if reading it cannot create a config file."""
    from config_manager import ConfigManager

    if ConfigManager._instance is None and not Path("config.yml").exists():
        return None
    try:
        return ConfigManager.instance()
    except Exception:  # pragma: no cover - a broken config must not break a tap
        log.debug("Telemetry could not read config", exc_info=True)
        return None


def _section() -> dict:
    cm = _cm()
    if cm is None:
        return {}
    try:
        return cm.get_telemetry()
    except Exception:  # pragma: no cover
        return {}


def _update(**changes) -> None:
    cm = _cm()
    if cm is None:
        from config_manager import get_config_manager

        cm = get_config_manager()
    cm.update_telemetry(**changes)
    refresh()


def _env_level() -> str | None:
    """The operator's override, or None when unset/nonsense."""
    from utils.config import CONDOR_TELEMETRY

    if CONDOR_TELEMETRY in LEVELS:
        return CONDOR_TELEMETRY
    if CONDOR_TELEMETRY:
        log.warning(
            "Ignoring CONDOR_TELEMETRY=%r: expected one of %s",
            CONDOR_TELEMETRY,
            ", ".join(LEVELS),
        )
    return None


def state() -> str:
    """``unknown`` | ``granted`` | ``denied``."""
    value = _section().get("consent")
    return value if value in (UNKNOWN, GRANTED, DENIED) else UNKNOWN


def level() -> str:
    """The effective level. Cached, because :func:`emit` reads it every time."""
    global _cached_level
    if _cached_level is None:
        _cached_level = _compute_level()
    return _cached_level


def _compute_level() -> str:
    env = _env_level()
    if env is not None:
        return env
    if state() == GRANTED:
        stored = _section().get("level")
        return stored if stored in (PING, USAGE) else USAGE
    # Unanswered — and legacy denied — installs are still counted: ping is the
    # floor, and only the environment can force `off`.
    return PING


def refresh() -> str:
    """Drop the cached level and recompute it. Called after any state change."""
    global _cached_level
    _cached_level = None
    return level()


def is_on() -> bool:
    return level() != OFF


def env_overridden() -> bool:
    """True when the operator has pinned the level, so no prompt should be sent."""
    return _env_level() is not None


# ── Identity ─────────────────────────────────────────────────────────────
# Both ids are random. Neither is derived from a MAC address, a hostname, a
# username or a token, so neither can be reversed into anything about the host.


def ensure_identity() -> dict:
    """Create the install's ids if they do not exist yet. Writes ``config.yml``.

    Called from :func:`condor.telemetry.init` only when telemetry is actually
    on, so a fresh install that never opts in never grows the section — and
    ``emit()`` never has to touch the disk to find an id.
    """
    section = _section()
    changes = {}
    if not section.get("install_id"):
        changes["install_id"] = uuid.uuid4().hex
    if not section.get("install_secret"):
        changes["install_secret"] = uuid.uuid4().hex
    if changes:
        _update(**changes)
        section = _section()
    return section


def install_id() -> str:
    return _section().get("install_id") or ""


def install_secret() -> str:
    """Never transmitted, never logged. Only ever salts a local hash."""
    return _section().get("install_secret") or ""


# ── Transitions ──────────────────────────────────────────────────────────


def grant(answer: str) -> str:
    """Record the admin's answer. ``answer`` is one of :data:`ANSWER_LEVELS`.

    Anything unrecognized — including an "off" tap on a prompt left over from
    an older version — lands on ``ping``, the floor, never silently on
    ``usage``.
    """
    chosen = ANSWER_LEVELS.get(answer, PING)
    _update(
        consent=GRANTED,
        level=chosen,
        decided_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    ensure_identity()
    return chosen


def _purge_collected() -> None:
    """Destroy everything recorded but not yet sent.

    Withdrawing usage consent deletes the spool and the outbox rather than
    merely ignoring them: a downgrade should leave nothing behind to be sent
    by a later bug.
    """
    from condor.telemetry import emitter, outbox

    emitter.discard_buffer()
    outbox.purge()


def set_level(new_level: str) -> str:
    """Change the level of an install. ``off`` is not accepted here: the floor
    is ``ping``, and only ``CONDOR_TELEMETRY=off`` reaches ``off``."""
    if new_level not in (PING, USAGE):
        return level()
    downgrading = new_level == PING and level() == USAGE
    _update(consent=GRANTED, level=new_level)
    ensure_identity()
    if downgrading:
        _purge_collected()
    return new_level


# ── Install counting ─────────────────────────────────────────────────────


def mark_install_reported() -> bool:
    """True the first time this install is counted, False forever after.

    The ``install`` event carries no properties — it *is* the count — so it has
    to be emitted exactly once per install, by whichever surface reaches it
    first: boot, the Telegram prompt, or the dashboard card.

    It used to be emitted only from the Telegram consent callback, which meant
    an install that never answered was never counted at all — and a local-mode
    install, which has no prompt to answer, could not be counted even in
    principle. ``ping`` is the floor, so counting needs no answer; this flag is
    what keeps it from being counted twice.
    """
    if _section().get("install_reported"):
        return False
    _update(install_reported=True)
    return True


# ── First-use tracking ───────────────────────────────────────────────────


def mark_feature_seen(feature: str) -> bool:
    """True the first time this install ever uses ``feature``, False after.

    Backs the ``feature_first_use`` activation funnel. Only called at level
    ``usage``, so an install that has not opted in never accumulates the list.
    """
    section = _section()
    seen = list(section.get("features_seen") or [])
    if feature in seen:
        return False
    seen.append(feature)
    _update(features_seen=seen[-200:])
    return True


# ── Prompt bookkeeping ───────────────────────────────────────────────────


def should_prompt(version: str = "") -> bool:
    """Has this install never been asked (or not since this version)?"""
    if env_overridden() or state() != UNKNOWN:
        return False
    asked = _section().get("prompted_version")
    return asked != (version or "unknown")


def mark_prompted(version: str = "") -> None:
    """Written *before* the prompt is sent, so a crash loop cannot re-ask forever."""
    _update(prompted_version=version or "unknown")
