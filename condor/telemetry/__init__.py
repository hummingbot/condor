"""Anonymous usage telemetry (FEAT-023).

Condor is self-hosted, so beyond knowing an install exists the project sees
nothing about how it is used unless the install chooses to tell it. This
package is that channel — and, because the same process holds exchange API
keys, it is built to be auditable in one sitting rather than to be clever.

The four facts that define it:

- **Every install that has not refused is counted.** The floor is level
  ``ping``: the four adoption events (``install``, ``heartbeat``,
  ``version_change``, ``shutdown``) and the anonymous envelope, from the first
  boot, with no answer required.
- **Usage is opt-in, once, by the admin.** One inline-keyboard prompt on boot
  offers full usage or install-count-only. The answer is durable and
  reversible.
- **Allowlisted.** :mod:`condor.telemetry.schema` declares every event and every
  property. Anything undeclared is dropped by construction, which is what makes
  the "never collected" list in ``PRIVACY.md`` a property of the code.
- **A refusal is honoured.** The collector address is fixed in
  :mod:`condor.telemetry.outbox`, so consent is the only switch there is. Level
  ``off`` — nothing is ever recorded — is reached two ways: the operator's
  ``CONDOR_TELEMETRY=off``, and an admin's recorded refusal
  (:func:`condor.telemetry.consent.deny`, behind Settings → Privacy), which is
  written to ``config.yml`` and therefore survives upgrades.

Public surface — call sites should need nothing else::

    from condor import telemetry
    telemetry.emit("command", name="portfolio", surface="telegram")
"""

from condor.telemetry.consent import (
    DENIED,
    GRANTED,
    OFF,
    PING,
    UNKNOWN,
    USAGE,
    is_on,
    level,
)
from condor.telemetry.consent import state as consent_state
from condor.telemetry.emitter import emit, flush

__all__ = [
    "emit",
    "flush",
    "level",
    "is_on",
    "consent_state",
    "init",
    "shutdown",
    "OFF",
    "PING",
    "USAGE",
    "UNKNOWN",
    "GRANTED",
    "DENIED",
]


def init(hosted: bool = True) -> str:
    """Prime this process. Returns the effective level.

    ``hosted`` marks a process that owns a flush job, so events buffer in memory
    instead of going straight to a spool file. The MCP subprocess passes False.

    Priming does three things and only three: it resolves the level once so
    :func:`emit` never has to read the disk on a hot path, and — only if the
    level is not ``off`` — it materializes the install's random ids and counts
    the install. With the ping floor all three happen on the very first boot;
    an install silenced by ``CONDOR_TELEMETRY=off`` or by a recorded refusal is
    left exactly as it was found — no ids, no count, no events.

    Counting here, rather than when the admin answers the prompt, is what makes
    "every install is counted" true rather than aspirational: an install whose
    admin ignores the prompt — and a local-mode install, which has no prompt to
    ignore — is still an install. ``mark_install_reported`` keeps it to once.
    """
    from condor.telemetry import consent, emitter

    emitter.set_hosted(hosted)
    effective = consent.refresh()
    if effective != consent.OFF:
        consent.ensure_identity()
        if consent.mark_install_reported():
            emit("install")
    return effective


def shutdown(reason: str = "signal") -> None:
    """Record that the process is going down. Sending is still the job's problem."""
    from condor.telemetry import context

    emit("shutdown", reason=reason, uptime_h=context.uptime_h())
