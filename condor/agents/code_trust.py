"""Per-file execution consent for code that came from outside this install.

An imported agent arrives with every routine muted, and the import says so:
*"Nothing is running. Read each imported routine and skill, then unmute the
ones you want this agent to use."* A mute, though, is subtracted **after**
discovery, and discovery is ``spec.loader.exec_module`` — so opening
``/routines``, reading a routine's source or rendering the brain panel used to
run the very code the operator was opening those surfaces to review
(FEAT-147).

This module is the record that makes the promise true. A routine file under an
**armed** agent is not imported until the operator has allowed *that file*,
identified by the sha256 of its text.

**Armed, not allowed-by-default.** The marker is the file's existence:
``store/code_trust.yml`` present means this agent's routines are gated, absent
means they are not. :func:`arm` is called in exactly one place —
``condor.agents.bundle.apply`` — so code that came from a bundle is gated and
an agent the operator wrote here is not. Gating a file somebody typed into
their own install would be a consent dialog for your own work.

**Why a digest and not just a name.** An installed agent can be updated from a
newer bundle (FEAT-145) keeping its mutes; consent keyed by name alone would
carry silently over to code the operator has never seen. Keyed by content, an
updated — or hand-edited — file is pending again, which is the right answer
for the same reason. The bundle format already carries sha256 digests, so the
idiom is the repo's own.

**Where it lives, and why there.** Under ``store/``, which
:func:`condor.layering.is_library_path` already refuses structurally: a bundle
carrying ``store/code_trust.yml`` is rejected by the import's existing path
guard, so a bundle can never arrive pre-allowed. A root file beside
``mutes.yml`` would have needed a new ``_RUNTIME_NAMES`` entry — one more
hand-kept list to forget.

**Unparseable means nothing allowed**, the inverse of ``load_mutes``'s choice
and for the inverse reason: there, a broken file costs an agent its library,
so the safe direction is "nothing muted"; here, a broken file would otherwise
run unreviewed code, so the safe direction is "nothing allowed".

Like :mod:`condor.memory.mutes` this is a leaf — ``yaml`` plus the path
resolver — so it reads from the main process and from the MCP subprocess
alike.

File shape::

    routines:
      lp_scanner: 9f2c…  # sha256 of the file's text when it was allowed
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from condor.fsutil import atomic_write_text
from condor.memory.paths import agent_home

#: The kinds consent can address. Only ``routines`` today: a skill is prose and
#: is never executed, and a controller runs inside the Hummingbot instance
#: rather than in this process. Written as a mapping from the start so a second
#: kind is a filter and not a file format.
KINDS = ("routines",)

FILENAME = "code_trust.yml"

#: Why a routine is not loadable, for the surface that has to say so.
PENDING = "pending"
CHANGED = "changed"
ALLOWED = "allowed"


class PendingConsentError(ValueError):
    """A routine was asked to run before the operator allowed its file."""


def trust_path(agent_slug: str | None) -> Path:
    """``agents/<slug>/store/code_trust.yml``."""
    return agent_home(agent_slug) / "store" / FILENAME


def digest_of(text: str) -> str:
    """The sha256 that identifies this exact file content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CodeTrust:
    """What one agent's operator has allowed to run, by file content."""

    routines: dict[str, str] = field(default_factory=dict)

    def state(self, name: str, text: str) -> str:
        """``allowed``, ``changed`` or ``pending`` for this file's content."""
        known = self.routines.get(name)
        if known is None:
            return PENDING
        return ALLOWED if known == digest_of(text) else CHANGED

    def permits(self, name: str, text: str) -> bool:
        """True only when this exact content was allowed under this name."""
        return self.state(name, text) == ALLOWED

    def fingerprint(self) -> tuple[tuple[str, str], ...]:
        """A cheap, hashable identity of the record, for a discovery cache."""
        return tuple(sorted(self.routines.items()))


def load(agent_slug: str | None) -> CodeTrust | None:
    """This agent's consent record, or ``None`` when it is not gated at all.

    ``None`` is the common case and the meaningful one: an agent nobody
    imported has no record, and every routine it owns loads exactly as before.
    """
    path = trust_path(agent_slug)
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, yaml.YAMLError):
        # Armed but unreadable: gated, with nothing allowed.
        return CodeTrust()
    if not isinstance(raw, dict):
        return CodeTrust()
    routines = raw.get("routines") or {}
    if not isinstance(routines, dict):
        return CodeTrust()
    return CodeTrust(
        routines={
            str(name): str(digest)
            for name, digest in routines.items()
            if name and digest
        }
    )


def is_armed(agent_slug: str | None) -> bool:
    """True when this agent's routines need consent before they execute."""
    return trust_path(agent_slug).exists()


def allowed(agent_slug: str | None, name: str, text: str) -> bool:
    """May this routine file execute? True for an agent that is not gated."""
    trust = load(agent_slug)
    return True if trust is None else trust.permits(name, text)


def state(agent_slug: str | None, name: str, text: str) -> str:
    """``allowed``, ``changed`` or ``pending`` — ``allowed`` when not gated."""
    trust = load(agent_slug)
    return ALLOWED if trust is None else trust.state(name, text)


def arm(agent_slug: str | None) -> None:
    """Gate this agent's routines: nothing it carries runs until it is allowed.

    Idempotent in the only direction that matters — an agent already gated
    keeps whatever has been allowed, so re-arming cannot silently revoke a
    decision the operator made.
    """
    if is_armed(agent_slug):
        return
    _save(agent_slug, CodeTrust())


def allow(agent_slug: str | None, name: str, text: str) -> str:
    """Record that this exact content may run. Returns the digest stored.

    Refuses on an agent that is not gated rather than arming it: writing the
    record would gate every *sibling* routine as a side effect of allowing
    one, which is the opposite of what the gesture says.
    """
    if not is_armed(agent_slug):
        raise PendingConsentError(
            f"'{agent_slug}' was not imported from a bundle, so its routines "
            "need no execution consent"
        )
    trust = load(agent_slug) or CodeTrust()
    stored = digest_of(text)
    routines = dict(trust.routines)
    routines[name] = stored
    _save(agent_slug, CodeTrust(routines=routines))
    return stored


def revoke(agent_slug: str | None, name: str) -> None:
    """Withdraw consent for one routine. The agent stays gated."""
    trust = load(agent_slug)
    if trust is None:
        return
    routines = dict(trust.routines)
    routines.pop(name, None)
    _save(agent_slug, CodeTrust(routines=routines))


def _save(agent_slug: str | None, trust: CodeTrust) -> None:
    """Write the record. Never deleted — its absence means *not gated*."""
    data = {kind: {} for kind in KINDS}
    data["routines"] = dict(sorted(trust.routines.items()))
    atomic_write_text(
        trust_path(agent_slug), yaml.safe_dump(data, default_flow_style=False)
    )
