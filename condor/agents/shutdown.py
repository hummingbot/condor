"""Emergency shutdown -- declarative winddown policy for a strategy.

When a strategy hits a kill-switch (a hard risk breach or a manual emergency
stop) its open **positions and executors** must be wound down, not left stranded.
The policy is declared per strategy in a ``shutdown.md`` file that reuses the exact
YAML-frontmatter + markdown-body format of ``strategy.md`` and the same
strategy-over-agent-over-default inheritance chain:

    agents/{slug}/strategies/{sslug}/shutdown.md   # this strategy
    agents/{slug}/shutdown.md                       # this agent (all its strategies)
    agents/_defaults/shutdown.md                    # shipped default

The front-matter is a machine-executable policy the deterministic winddown reads;
the body is free-form instructions handed to the bounded LLM cleanup pass.
"""

from __future__ import annotations

import logging

from .strategy import Strategy, _parse_frontmatter

log = logging.getLogger(__name__)

# on_kill_switch policy values. Default matches the user's framing of a kill
# switch: drop the dangerous leveraged risk (perp) without force-selling spot.
POLICY_FLATTEN_ALL = "flatten_all"
POLICY_KEEP_SPOT_CLOSE_PERP = "keep_spot_close_perp"
POLICY_KEEP_ALL = "keep_all"
VALID_POLICIES = (POLICY_FLATTEN_ALL, POLICY_KEEP_SPOT_CLOSE_PERP, POLICY_KEEP_ALL)
DEFAULT_POLICY = POLICY_KEEP_SPOT_CLOSE_PERP


class ShutdownPolicy:
    """Machine-executable winddown policy parsed from ``shutdown.md`` front-matter."""

    def __init__(
        self,
        on_kill_switch: str = DEFAULT_POLICY,
        cancel_open_orders: bool = True,
    ):
        self.on_kill_switch = on_kill_switch
        self.cancel_open_orders = cancel_open_orders

    @classmethod
    def from_dict(cls, d: dict) -> "ShutdownPolicy":
        policy = str((d or {}).get("on_kill_switch", DEFAULT_POLICY)).strip()
        if policy not in VALID_POLICIES:
            log.warning(
                "Unknown shutdown policy %r; falling back to %s", policy, DEFAULT_POLICY
            )
            policy = DEFAULT_POLICY
        return cls(
            on_kill_switch=policy,
            cancel_open_orders=bool((d or {}).get("cancel_open_orders", True)),
        )

    def to_dict(self) -> dict:
        return {
            "on_kill_switch": self.on_kill_switch,
            "cancel_open_orders": self.cancel_open_orders,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"ShutdownPolicy(on_kill_switch={self.on_kill_switch!r}, "
            f"cancel_open_orders={self.cancel_open_orders})"
        )


def load_shutdown_policy(strategy: Strategy) -> tuple[ShutdownPolicy, str]:
    """Resolve the shutdown policy + LLM body for ``strategy``.

    Walks strategy → agent → shipped default, returning the first ``shutdown.md``
    found. Paths are derived from ``strategy.dir`` (``.../agents/{slug}/strategies/
    {sslug}``) so the resolution follows the same (possibly test-patched) data root
    as the rest of the agent store. If nothing is on disk, returns the built-in
    default policy with an empty body.
    """
    # strategy.dir == {root}/{agent_slug}/strategies/{sslug}
    agent_dir = strategy.dir.parent.parent  # {root}/{agent_slug}
    data_root = agent_dir.parent  # {root}
    candidates = [
        strategy.dir / "shutdown.md",
        agent_dir / "shutdown.md",
        data_root / "_defaults" / "shutdown.md",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            meta, body = _parse_frontmatter(path.read_text())
            return ShutdownPolicy.from_dict(meta), body.strip()
        except Exception:
            log.exception("Failed to parse shutdown.md at %s", path)
    return ShutdownPolicy(), ""
