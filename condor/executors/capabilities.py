"""Creation capabilities — execution authority comes from context, not caller
fields (§6.2).

ExecutionService.create requires a creation context minted by the platform:

- **Agent run capability** — minted by AgentService at ``run()``, carrying
  the frozen spec's identity (slug, run_id) + risk policy. Injected into the
  launched tool session's environment as an opaque id; the MCP wrapper passes
  only that id over the control socket; ExecutionService derives origin,
  run, agent, AccountRef, and risk policy SOLELY from the server-side entry.
  Invalidated when its run ends.
- **Condor-direct capability** — the human acting through their harness
  chat/dashboard. Bootstrap: the persistent runtime host writes a
  per-process direct-session token to a 0600 file (``store/.direct-token``,
  rotated every restart); the MCP wrapper reads it (same OS user = the
  human) and presents it ONCE over a persistent control connection to
  register, receiving a condor-direct capability whose lifetime is bound to
  that connection — the capability is revoked the moment the connection
  closes, and on process restart (token rotation).

**Absence of a capability is a rejection, never a fallback to condor-direct.**
A session already holding a run capability cannot register as condor-direct
(the escalation path is rejected and recorded).
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_STORE_DIR = Path(__file__).resolve().parents[2] / "store"
DIRECT_TOKEN_PATH = _STORE_DIR / ".direct-token"

ENV_CAPABILITY = "CONDOR_RUN_CAPABILITY"  # injected into agent tool sessions


class CapabilityError(Exception):
    """Missing/unknown/expired/mismatched capability — always a rejection."""


@dataclass
class Capability:
    id: str
    origin: str  # "agent" | "condor"
    agent_slug: str = ""  # agent origin only
    run_id: str = ""  # agent origin only ("{slug}_{N}" today; ULID in Phase 4)
    risk_limits: dict = field(default_factory=dict)  # frozen run risk policy
    # condor-direct only: the persistent control-connection id whose closure
    # revokes this capability.
    connection_id: str = ""


class CapabilityRegistry:
    """Server-side registry of live creation capabilities (opaque ids)."""

    def __init__(self):
        self._caps: dict[str, Capability] = {}
        self._lock = threading.Lock()

    # -- agent run capabilities --

    def mint_run_capability(
        self, agent_slug: str, run_id: str, risk_limits: dict | None = None
    ) -> Capability:
        cap = Capability(
            id=secrets.token_urlsafe(24),
            origin="agent",
            agent_slug=agent_slug,
            run_id=run_id,
            risk_limits=dict(risk_limits or {}),
        )
        with self._lock:
            self._caps[cap.id] = cap
        log.info("run capability minted for %s", run_id)
        return cap

    def revoke_run(self, run_id: str) -> None:
        """Invalidate every capability of an ended run."""
        with self._lock:
            dead = [cid for cid, c in self._caps.items() if c.run_id == run_id]
            for cid in dead:
                del self._caps[cid]
        if dead:
            log.info("revoked %d capability(ies) for ended run %s", len(dead), run_id)

    # -- condor-direct bootstrap --

    def write_direct_token(self) -> str:
        """Write the per-process direct-session token (0600, rotated every
        restart). Called once by the persistent runtime host at startup."""
        _STORE_DIR.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        DIRECT_TOKEN_PATH.write_text(token)
        os.chmod(DIRECT_TOKEN_PATH, 0o600)
        self._direct_token = token
        return token

    def register_direct(
        self, presented_token: str, connection_id: str, *, session_capability: str = ""
    ) -> Capability:
        """Exchange the direct token for a condor-direct capability bound to a
        persistent control connection.

        ``session_capability``: if the presenting session already holds a RUN
        capability id, the downgrade is rejected and recorded — an agent
        session must never masquerade as the human.
        """
        if session_capability:
            with self._lock:
                existing = self._caps.get(session_capability)
            if existing is not None and existing.origin == "agent":
                log.warning(
                    "condor-direct registration REJECTED: session holds run "
                    "capability for %s (escalation attempt recorded)",
                    existing.run_id,
                )
                raise CapabilityError(
                    "a session holding an agent-run capability cannot "
                    "register as condor-direct"
                )
        expected = getattr(self, "_direct_token", None)
        if not expected or not secrets.compare_digest(presented_token, expected):
            raise CapabilityError("invalid direct-session token")
        cap = Capability(
            id=secrets.token_urlsafe(24),
            origin="condor",
            connection_id=connection_id,
        )
        with self._lock:
            self._caps[cap.id] = cap
        log.info("condor-direct capability registered (conn %s)", connection_id)
        return cap

    def revoke_connection(self, connection_id: str) -> None:
        """Called when a persistent control connection closes — SIGKILLed
        wrappers drop the socket and lose their capability immediately."""
        with self._lock:
            dead = [
                cid
                for cid, c in self._caps.items()
                if c.connection_id and c.connection_id == connection_id
            ]
            for cid in dead:
                del self._caps[cid]
        if dead:
            log.info(
                "revoked %d condor-direct capability(ies) on connection close %s",
                len(dead),
                connection_id,
            )

    # -- resolution (the ONLY way ExecutionService derives attribution) --

    def resolve(self, capability_id: Optional[str]) -> Capability:
        """Resolve an opaque capability id to its server-side entry.

        Absence, unknown ids, and expired (revoked) ids are all rejections —
        never a fallback to condor-direct.
        """
        if not capability_id:
            raise CapabilityError(
                "no creation capability presented — executor creation "
                "requires an agent-run capability (injected at run()) or a "
                "registered condor-direct session"
            )
        with self._lock:
            cap = self._caps.get(capability_id)
        if cap is None:
            raise CapabilityError(
                "unknown or expired creation capability — the run may have "
                "ended (relaunch mints a fresh capability) or the direct "
                "session's connection closed"
            )
        return cap

    def clear(self) -> None:
        with self._lock:
            self._caps.clear()


# Process-global registry (the persistent runtime host owns it).
_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
    return _registry
