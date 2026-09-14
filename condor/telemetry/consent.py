"""Consent: the machine that decides how much an install says.

The install — not the individual user — is the unit of consent, because the
admin owns the install. The states are ``unknown`` (the default on a fresh
clone), ``granted`` and ``denied``, stored in ``config.yml`` under ``telemetry``
alongside the install's identity.

Four rules matter more than the rest:

**The floor is ``ping`` — until the admin has been told.** An install that has
said nothing emits the four adoption events (``install``, ``heartbeat``,
``version_change``, ``shutdown``) and nothing else. That is all an install
sends before anyone could have read what it sends.

**Usage is the default once the notice has been shown.** The admin is shown one
notice — the Telegram message next to the boot notification, or the dashboard
strip — saying usage summaries are on and where to turn them off. Its delivery
is recorded as ``noticed_at``, and from that moment an unanswered install
resolves to ``usage``. It is an opt-out, and the notice is what makes it one: no
usage event is ever recorded for an install that was never told. "Got it" just
records ``granted`` at ``usage`` so the notice stops showing.

**A refusal is honoured, and survives an upgrade.** ``denied`` is not silence:
it is a recorded "no", written by :func:`deny` (the notice's "Turn off", the
dashboard's off switch, older builds' "No thanks"). It resolves to level
``off``, so an install that refused under any build stays silent across upgrades
and is never re-asked — and the notice cannot flip it, nor an install that chose
``ping``. Re-enabling is an explicit act — :func:`set_level`.

**The environment wins.** ``CONDOR_TELEMETRY`` in ``utils/config.py`` overrides
the stored answer in both directions: it can silence an install that granted
consent, and it can re-enable one that refused.

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

# Answer -> level for the grantable answers: the notice's "Got it" and the two
# levels in Settings. "off" is deliberately not one of them — refusing is a
# separate, recorded act, `deny()`, so a stray answer can never be read as one.
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
    stored_state = state()
    if stored_state == GRANTED:
        stored = _section().get("level")
        return stored if stored in (PING, USAGE) else USAGE
    if stored_state == DENIED:
        # A recorded "no" outranks everything stored. Installs that refused
        # under an older build carry this state forward, and an upgrade must not
        # read their refusal as an unanswered notice.
        return OFF
    # Unanswered: usage once the admin has been told it is on, and until then
    # only the ping floor — nothing beyond a count before anyone could know.
    return USAGE if notice_shown() else PING


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
    """Change the level of an install to one of the two *grantable* answers.

    ``off`` is not one of them: it is a refusal, not a level, so it goes
    through :func:`deny` instead — which records *why* the install is silent so
    the next upgrade honours it. Calling this with ``ping`` or ``usage`` is also
    how a refusing install opts back in.
    """
    if new_level not in (PING, USAGE):
        return level()
    downgrading = new_level == PING and level() == USAGE
    _update(consent=GRANTED, level=new_level)
    ensure_identity()
    if downgrading:
        _purge_collected()
    return new_level


def deny() -> str:
    """Record an explicit refusal, and destroy whatever was already collected.

    This is the in-product off switch. It is durable in a way the environment
    kill switch is not: ``CONDOR_TELEMETRY=off`` silences the process that
    happens to read it, while ``denied`` is written to ``config.yml``, resolves
    to ``off`` in :func:`_compute_level`, and stops :func:`should_prompt` from
    ever asking again — so the refusal survives upgrades instead of being
    re-read as silence.

    Refusing deletes the spool and the outbox rather than merely ignoring them:
    a "no" should leave nothing behind for a later bug to send. It deliberately
    does not call :func:`ensure_identity` — an install that refuses should not
    grow an install id on its way out.
    """
    _update(
        consent=DENIED,
        level=OFF,
        decided_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    _purge_collected()
    return OFF


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
    ``usage``, so an install below it never accumulates the list.
    """
    section = _section()
    seen = list(section.get("features_seen") or [])
    if feature in seen:
        return False
    seen.append(feature)
    _update(features_seen=seen[-200:])
    return True


# ── Notice bookkeeping ───────────────────────────────────────────────────


def notice_shown() -> bool:
    """Has the admin been shown the notice that usage summaries are on?"""
    return bool(_section().get("noticed_at"))


def mark_notice_shown() -> bool:
    """Record that the notice reached the admin. True if this turned usage on.

    Called only once the notice has actually been delivered — after the
    Telegram message was sent, or when the dashboard strip rendered for the
    admin — never before, because this is the moment an unanswered install
    starts sending usage events. It changes nothing for an install that has
    already answered (a ``ping`` or a refusal stays exactly that) or whose level
    the environment pins.
    """
    if env_overridden() or state() != UNKNOWN or notice_shown():
        return False
    _update(noticed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    ensure_identity()
    return True


def should_prompt(version: str = "") -> bool:
    """Is there a notice left to send (and not already tried on this version)?"""
    if env_overridden() or state() != UNKNOWN or notice_shown():
        return False
    asked = _section().get("prompted_version")
    return asked != (version or "unknown")


def mark_prompted(version: str = "") -> None:
    """Written *before* the notice is sent, so a crash loop cannot re-send forever.

    Deliberately not :func:`mark_notice_shown`: an attempt is not a delivery,
    and a send that fails must leave the install at the ping floor.
    """
    _update(prompted_version=version or "unknown")
