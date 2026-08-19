"""Anonymous, opt-in usage telemetry (FEAT-023).

Condor is self-hosted, so the project sees nothing about how installs are used
unless an install chooses to tell it. This package is that channel — and,
because the same process holds exchange API keys, it is built to be auditable in
one sitting rather than to be clever.

The four facts that define it:

- **Off by default.** A fresh clone has no consent recorded, which resolves to
  level ``off``, at which :func:`emit` returns before doing anything at all. No
  buffer, no file, no directory.
- **Opt-in, once, by the admin.** One inline-keyboard prompt on boot offers
  full usage, install-count-only, or no. The answer is durable and reversible.
- **Allowlisted.** :mod:`condor.telemetry.schema` declares every event and every
  property. Anything undeclared is dropped by construction, which is what makes
  the "never collected" list in ``PRIVACY.md`` a property of the code.
- **Consent is the only switch.** The collector address is fixed in
  :mod:`condor.telemetry.outbox`; at level ``off`` nothing is ever recorded, so
  there is nothing to send it.

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

    Priming does two things and only two: it resolves the level once so
    :func:`emit` never has to read the disk on a hot path, and — only if the
    level is not ``off`` — it materializes the install's random ids. An install
    that never opted in is left exactly as it was found.
    """
    from condor.telemetry import consent, emitter

    emitter.set_hosted(hosted)
    effective = consent.refresh()
    if effective != consent.OFF:
        consent.ensure_identity()
    return effective


def shutdown(reason: str = "signal") -> None:
    """Record that the process is going down. Sending is still the job's problem."""
    from condor.telemetry import context

    emit("shutdown", reason=reason, uptime_h=context.uptime_h())
