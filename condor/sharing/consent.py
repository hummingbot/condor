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

``user_state`` has three answers. ``off`` is the default and the only one a
fresh install has. ``explicit`` and ``off`` behave identically for the button —
pressing it *is* the consent, and it is recorded on the conversation rather than
on the user. ``always`` is the standing answer FEAT-055 added: it is the only
thing that lets the sweep in :mod:`condor.sharing.sweep` send anything without a
human looking at the payload first, which is why it is the only state that
records *when* it was chosen. ``opted_in_at`` is what makes forward-only
enforceable rather than aspirational — a conversation older than the answer was
never covered by it.

The identity is this package's own and deliberately not the telemetry install
id. Two consequences, both wanted: an install with ``CONDOR_TELEMETRY=off`` can
still share a conversation, and a shared transcript cannot be joined to that
install's heartbeat history. What "which build produced this" actually needs is
:func:`condor.telemetry.context.app`, which the envelope carries instead.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

OFF = "off"
EXPLICIT = "explicit"
ALWAYS = "always"  # The standing answer the sweep acts on (FEAT-055).
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
    """Record the admin's answer. Install-wide, and reversible.

    A veto is retroactive: it destroys the shares already queued rather than
    merely deciding not to send them, the same way telemetry's ``_purge_collected``
    empties its spool on withdrawal. The switch is reversible, so anything left
    behind would ship the moment somebody turned sharing back on — long after the
    conversations were queued and with nobody looking. Pending *unshares* survive
    it; see :func:`condor.sharing.outbox.purge_shares` for why.
    """
    _update(enabled=bool(enabled))
    if not enabled:
        from condor.sharing import outbox

        outbox.purge_shares()
    return install_allows()


def _record(user_id: int | str) -> dict:
    """This user's stored answer, in either shape it can be on disk.

    FEAT-054 wrote a bare string per user. FEAT-055 needs a timestamp beside the
    answer, so the value is now a mapping — and a string is still read as one,
    because a config written by the older build must keep meaning what it meant.
    A string is only ever ``off`` or ``explicit`` there, neither of which the
    sweep acts on, so there is nothing to migrate and nothing is rewritten until
    the user next chooses.
    """
    states = _section().get("users") or {}
    value = states.get(str(user_id))
    if value is None:
        value = states.get(user_id)
    if isinstance(value, str):
        return {"state": value}
    return dict(value) if isinstance(value, dict) else {}


def user_state(user_id: int | str) -> str:
    """``off`` (the default) | ``explicit`` | ``always``.

    ``off`` and ``explicit`` differ only as a record of what the user last chose
    — the button works at either, because pressing it *is* the consent. Only
    ``always`` changes what the install does on its own.
    """
    value = _record(user_id).get("state")
    return value if value in USER_STATES else OFF


def opted_in_at(user_id: int | str) -> float:
    """When this user chose ``always``, as a Unix timestamp. ``0.0`` if never.

    Forward-only lives or dies on this number: the sweep will not take a
    conversation created before it. A user at any other state has no such
    timestamp, so the sweep has nothing to compare against and takes nothing —
    which is the correct reading of "I have not opted in".
    """
    if user_state(user_id) != ALWAYS:
        return 0.0
    try:
        return float(_record(user_id).get("opted_in_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def set_user_state(user_id: int | str, state: str) -> str:
    """Record a standing answer, and stamp ``always`` with the moment it began.

    Re-choosing ``always`` while already at ``always`` keeps the original
    timestamp: the consent has been continuous, and moving it forward would
    silently make the conversations in between ineligible. Leaving ``always``
    drops it, so opting back in later covers only what comes after *that* — a
    gap in consent is a gap in the corpus, not something to paper over.
    """
    if state not in USER_STATES:
        return user_state(user_id)

    record = _record(user_id) if state == ALWAYS else {}
    record["state"] = state
    if state == ALWAYS and not record.get("opted_in_at"):
        record["opted_in_at"] = time.time()

    states = dict(_section().get("users") or {})
    # YAML can hand back an integer key for a numeric user id, and a stale one
    # left beside the string form would shadow this write on the next read.
    states.pop(user_id, None)
    states[str(user_id)] = record
    _update(users=states)
    return state


def can_share(user_id: int | str) -> bool:
    """May *this* user share *now*? Every gate, in precedence order.

    The user's own state is deliberately not one of the gates: at ``off`` — the
    default — pressing the button is still allowed, because pressing it *is* the
    consent. What the state gates is the automatic producer, in
    :func:`can_sweep`.
    """
    if not env_allows() or not install_allows():
        return False
    try:
        return int(user_id) > 0
    except (TypeError, ValueError):
        return False


def can_sweep(user_id: int | str) -> bool:
    """May the install share this user's conversations *without being asked*?

    Strictly narrower than :func:`can_share`, and that is the whole point: this
    is the only predicate in the package that authorizes sending a payload no
    human has looked at. It requires everything the button requires **and** a
    standing ``always`` with a timestamp to enforce forward-only against, so the
    operator kill switch and the admin veto both outrank a user's Always exactly
    as they outrank their button.
    """
    return can_share(user_id) and user_state(user_id) == ALWAYS


def users_sweeping() -> list[str]:
    """Every user id with a standing ``always``, from the stored answers alone.

    Nothing intersects this with the ids that have a runtime directory:
    ``sweep._candidates`` walks this list and calls ``sweep.eligible`` on each
    one directly. A user who consented and then had their directory removed is
    absorbed a layer down, where
    :func:`condor.runtime.conversations.list_conversations` returns ``[]`` for a
    missing directory — so a stale id costs a lookup and nothing else.
    """
    states = _section().get("users") or {}
    return [uid for uid in map(str, states) if user_state(uid) == ALWAYS]


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
