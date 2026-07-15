"""Instrument leases — at most ONE Condor actor per (AccountRef, instrument).

Simplification plan §6.2/§6.2b: "Condor actor" covers BOTH agent runs and
``origin: condor`` direct trades — a direct create on an instrument an agent
is actively working is lease-rejected (stop the agent first), and vice
versa. All instrument types, one rule (matters most on netted venues,
harmless elsewhere). The warning layer covers what the lease cannot:
out-of-band activity Condor never sees at order time.

Leases are IN-MEMORY, rebuilt at startup from nonterminal executor records
(the durable authority) before readiness opens — see the §12 startup
ordering. A lease is held from executor create until the executor reaches a
terminal state INCLUDING confirmed-terminal cancellations (§6.2 stop
semantics: the lease releases only after every owned cancellation confirms).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from condor.accounts.model import AccountRef

log = logging.getLogger(__name__)


class LeaseConflict(Exception):
    """Another Condor actor already holds this (AccountRef, instrument)."""

    def __init__(self, key, holder: str, requester: str):
        self.key = key
        self.holder = holder
        self.requester = requester
        super().__init__(
            f"instrument lease conflict on {key[0]}:{key[1]}: held by "
            f"{holder!r}, requested by {requester!r} — one Condor actor per "
            "(account, instrument); stop the other actor first"
        )


def lease_key(account_ref: AccountRef, instrument_id: str) -> tuple[str, str]:
    """The one lease key: (canonical account, canonical instrument id)."""
    return (str(account_ref), instrument_id)


@dataclass
class _Lease:
    owner: str  # actor id: run_id ("{slug}_{N}") or "condor" for direct
    holders: set[str] = field(default_factory=set)  # executor_ids holding it


class LeaseManager:
    """Same-instrument race protection between Condor actors.

    An actor may hold a lease through several executors (e.g. two resting
    quotes on one instrument); the lease releases when its LAST holder
    reaches terminal state. A DIFFERENT actor acquiring the same key is
    rejected.
    """

    def __init__(self):
        self._leases: dict[tuple[str, str], _Lease] = {}
        self._lock = threading.Lock()

    def acquire(
        self,
        account_ref: AccountRef,
        instrument_id: str,
        *,
        owner: str,
        executor_id: str,
    ) -> None:
        """Acquire (or join) the lease for ``owner``; raise LeaseConflict if a
        different actor holds it."""
        key = lease_key(account_ref, instrument_id)
        with self._lock:
            lease = self._leases.get(key)
            if lease is None:
                self._leases[key] = _Lease(owner=owner, holders={executor_id})
                log.info("lease acquired %s by %s (%s)", key, owner, executor_id)
                return
            if lease.owner != owner:
                raise LeaseConflict(key, lease.owner, owner)
            lease.holders.add(executor_id)

    def release(
        self, account_ref: AccountRef, instrument_id: str, *, executor_id: str
    ) -> None:
        """Release one holder; the lease disappears with its last holder."""
        key = lease_key(account_ref, instrument_id)
        with self._lock:
            lease = self._leases.get(key)
            if lease is None:
                return
            lease.holders.discard(executor_id)
            if not lease.holders:
                del self._leases[key]
                log.info("lease released %s (last holder %s)", key, executor_id)

    def holder(self, account_ref: AccountRef, instrument_id: str) -> str | None:
        """The owning actor id, or None."""
        lease = self._leases.get(lease_key(account_ref, instrument_id))
        return lease.owner if lease else None

    def snapshot(self) -> dict[tuple[str, str], str]:
        with self._lock:
            return {k: v.owner for k, v in self._leases.items()}

    def clear(self) -> None:
        with self._lock:
            self._leases.clear()
