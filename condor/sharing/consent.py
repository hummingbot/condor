"""Consent: who may hand a conversation to the project.

Three actors, and each one can only say no to what is actually theirs:

**The operator** sets ``CONDOR_SHARING=off`` in the environment and nothing on
this box can share anything, which is the same precedence ``CONDOR_TELEMETRY``
has in :mod:`condor.telemetry.consent` — an environment that pins a policy
outranks anything written in ``config.yml``.

**The admin** owns the install's egress, so they hold an install-wide veto
(``config.yml`` → ``sharing.enabled``). It defaults to *on*: the veto is a
switch an admin reaches for, not a second opt-in a user has to chase, because
nothing is ever sent without a user pressing a button anyway.

**The user** owns the chat, so the user is the one who consents to it leaving.
That is the difference from telemetry and the reason this module exists rather
than reusing that one: telemetry consent is *per install*, held by the admin,
and an admin tapping "yes" on an install-wide prompt cannot consent to
uploading another person's conversation.

In this feature ``user_state`` is only ever ``off`` or ``explicit``: pressing
the button *is* the consent, and it is recorded on the conversation rather than
on the user. ``always`` exists here because the state machine is this module's
to own, but nothing sets it — the per-user "share everything from now on"
opt-in and the sweep that acts on it are FEAT-055.

The identity is this package's own and deliberately not the telemetry install
id. Two consequences, both wanted: an install with ``CONDOR_TELEMETRY=off`` can
still share a conversation, and a shared transcript cannot be joined to that
install's heartbeat history. What "which build produced this" actually needs is
:func:`condor.telemetry.context.app`, which the envelope carries instead.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

OFF = "off"
EXPLICIT = "explicit"
ALWAYS = "always"  # FEAT-055 sets this; nothing in this feature does.
USER_STATES = (OFF, EXPLICIT, ALWAYS)

SECTION = "sharing"
ENV_VAR = "CONDOR_SHARING"


def _cm():
    """The ConfigManager, but only if reading it cannot create a config file.

    Same guard as telemetry's: a read must never be the thing that materializes
    ``config.yml`` on a fresh clone.
    """
    from config_manager import ConfigManager

    if ConfigManager._instance is None and not Path("config.yml").exists():
        return None
    try:
        return ConfigManager.instance()
    except Exception:  # pragma: no cover - a broken config must not break a share
        log.debug("Sharing could not read config", exc_info=True)
        return None


def _section() -> dict:
    cm = _cm()
    if cm is None:
        return {}
    try:
        return cm.get_sharing()
    except Exception:  # pragma: no cover
        return {}


def _update(**changes) -> dict:
    cm = _cm()
    if cm is None:
        from config_manager import get_config_manager

        cm = get_config_manager()
    return cm.update_sharing(**changes)


# ── The three gates ──────────────────────────────────────────────────────


def env_allows() -> bool:
    """``CONDOR_SHARING=off`` silences this process, whatever config.yml says.

    Read from the environment on every call rather than cached at import: unlike
    telemetry's level, this is not on a hot path — it is consulted once per share
    — and an operator who exports it should not have to restart to be obeyed.
    """
    value = (os.environ.get(ENV_VAR, "") or "").strip().lower()
    if value in ("off", "0", "false", "no"):
        return False
    if value and value not in ("on", "1", "true", "yes"):
        log.warning("Ignoring %s=%r: expected on or off", ENV_VAR, value)
    return True


def env_overridden() -> bool:
    """True when the operator has pinned the answer, so no UI may change it."""
    return (os.environ.get(ENV_VAR, "") or "").strip().lower() in (
        "off",
        "0",
        "false",
        "no",
    )


def install_allows() -> bool:
    """The admin's veto. Default on — see the module docstring for why."""
    enabled = _section().get("enabled")
    return True if enabled is None else bool(enabled)


def set_install_allows(enabled: bool) -> bool:
    """Record the admin's answer. Install-wide, and reversible."""
    _update(enabled=bool(enabled))
    return install_allows()


def user_state(user_id: int | str) -> str:
    """``off`` (the default) | ``explicit`` | ``always``.

    Nothing in FEAT-054 moves a user off ``off``: the button is consent for one
    conversation, recorded on that conversation. This exists so FEAT-055 has a
    place to put a standing answer, and so ``can_share`` has one thing to read.
    """
    states = _section().get("users") or {}
    value = states.get(str(user_id)) or states.get(user_id)
    return value if value in USER_STATES else OFF


def set_user_state(user_id: int | str, state: str) -> str:
    if state not in USER_STATES:
        return user_state(user_id)
    states = dict(_section().get("users") or {})
    states[str(user_id)] = state
    _update(users=states)
    return state


def can_share(user_id: int | str) -> bool:
    """May *this* user share *now*? Every gate, in precedence order.

    The user's own state is deliberately not one of the gates: at ``off`` — the
    default, and where FEAT-054 leaves everyone — pressing the button is still
    allowed, because pressing it *is* the consent. What the state will gate is
    the automatic producer FEAT-055 adds.
    """
    if not env_allows() or not install_allows():
        return False
    try:
        return int(user_id) > 0
    except (TypeError, ValueError):
        return False


# ── Identity ─────────────────────────────────────────────────────────────
# Both ids are random and this install's own. Neither is derived from a MAC
# address, a hostname, a username or a token, and neither is the telemetry
# install id — see the module docstring.


def ensure_identity() -> dict:
    """Mint this install's sharing ids if they do not exist. Writes config.yml.

    Called from the share path only, so an install that never shares anything
    never grows the section.
    """
    section = _section()
    changes = {}
    if not section.get("share_install_id"):
        changes["share_install_id"] = uuid.uuid4().hex
    if not section.get("share_secret"):
        changes["share_secret"] = uuid.uuid4().hex
    if changes:
        section = _update(**changes)
    return section


def share_install_id() -> str:
    return _section().get("share_install_id") or ""


def share_secret() -> str:
    """Never transmitted, never logged, never rendered.

    It salts the pseudonym HMAC in :mod:`condor.sharing.scrub`, which is the
    only reason it exists: because it stays here, the mapping from a wallet to
    ``SOL_ADDR_a3f91c`` is not reversible by whoever reads the corpus, and two
    installs give the same address two different pseudonyms.
    """
    return _section().get("share_secret") or ""
