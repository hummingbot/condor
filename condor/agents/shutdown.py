"""Emergency shutdown -- declarative winddown policy for an agent session.

When a session hits a kill-switch (a hard risk breach or a manual emergency
stop) its open **positions and executors** must be wound down, not left stranded.
The policy is declared per agent in a ``shutdown.md`` file that reuses the exact
YAML-frontmatter + markdown-body format of ``AGENT.md``:

    agents/{slug}/shutdown.md      # this agent (all its sessions)
    agents/_defaults/shutdown.md   # shipped default

(Winddown is session-scoped and positions don't care which playbook opened
them, so there is no per-strategy tier — refactor-01b.)

The front-matter is a machine-executable policy the deterministic winddown reads;
the body is free-form instructions handed to the bounded LLM cleanup pass.

The winddown runs on the NATIVE executor runtime (``condor.executors``): the
slug's nonterminal records are read from the executor store and stopped through
``condor.executors.ops.stop`` — no external API in the loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from condor.executors import ops
from condor.executors.service import peek_executor_runtime
from condor.memory.store import _parse_frontmatter

from .agent import Agent

log = logging.getLogger(__name__)

# on_kill_switch policy values. Default matches the user's framing of a kill
# switch: drop the dangerous leveraged risk (perp) without force-selling spot.
POLICY_FLATTEN_ALL = "flatten_all"
POLICY_KEEP_SPOT_CLOSE_PERP = "keep_spot_close_perp"
POLICY_KEEP_ALL = "keep_all"
VALID_POLICIES = (POLICY_FLATTEN_ALL, POLICY_KEEP_SPOT_CLOSE_PERP, POLICY_KEEP_ALL)
DEFAULT_POLICY = POLICY_KEEP_SPOT_CLOSE_PERP

# Executor-record statuses that mean "still holding/able to hold inventory".
_NONTERMINAL = {"PENDING", "ACTIVE", "CLOSING"}

# How long stops get to settle (executor loops close asynchronously) before the
# verify re-read. Module constant so tests can zero it.
_SETTLE_DELAY_S = 2.0


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


def load_shutdown_policy(agent: Agent) -> tuple[ShutdownPolicy, str]:
    """Resolve the shutdown policy + LLM body for ``agent``.

    Walks agent → shipped default, returning the first ``shutdown.md`` found.
    Paths are derived from ``agent.agent_dir`` so the resolution follows the
    same (possibly test-patched) data root as the rest of the agent store. If
    nothing is on disk, returns the built-in default policy with an empty body.
    """
    agent_dir = agent.agent_dir  # {root}/{agent_slug}
    data_root = agent_dir.parent  # {root}
    candidates = [
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


# ---------------------------------------------------------------------------
# Deterministic winddown -- the safety-critical floor
# ---------------------------------------------------------------------------
#
# This runs first and without any LLM, so a kill switch is guaranteed to act even
# when the model or market is misbehaving (the exact conditions that trigger it).
# The reliable close primitive is ``ops.stop(keep_position=False)``, which closes
# the position the executor holds. Genuine orphan inventory (open on venue, no
# executor) is left to the LLM cleanup pass and, if a record the policy said to
# close persists, the verify step raises a loud alert.


def _is_perp(executor_type: str) -> bool:
    """Whether an executor type carries perpetual/leveraged risk.

    Condor-native executors encode the instrument in the type suffix
    (``position_perp`` / ``order_perp`` vs ``position_spot`` / ``order_pred``).
    Unknown/ambiguous types are treated as perp so a kill switch errs toward
    *closing* leveraged risk rather than leaving it open.
    """
    t = (executor_type or "").lower()
    if not t:
        return True
    if t.endswith("_perp"):
        return True
    # Only the known non-leveraged suffixes count as "not perp" — everything
    # else fails closed.
    return not (t.endswith("_spot") or t.endswith("_pred"))


def _keep_position(record: Any, policy: ShutdownPolicy) -> bool:
    """Whether stopping ``record``'s executor should keep its position."""
    if policy.on_kill_switch == POLICY_FLATTEN_ALL:
        return False
    if policy.on_kill_switch == POLICY_KEEP_ALL:
        return True
    # keep_spot_close_perp: keep spot, close perp
    return not _is_perp(getattr(record, "type", ""))


# Position-executor states that mean inventory is (still) on the venue.
_LIVE_POSITION_STATES = {"OPENING", "ACTIVE", "CLOSING"}


def _holds_inventory(record: Any) -> bool:
    """Whether a position-kind record's state still holds venue inventory.

    A closed-with-``detached`` record ended its loop but deliberately left the
    position on the venue — stranded whenever the policy said to close it.
    """
    if not str(getattr(record, "type", "")).startswith("position"):
        return False
    state = getattr(record, "state", None) or {}
    if str(state.get("state") or "") in _LIVE_POSITION_STATES:
        return True
    return state.get("close_type") == "detached"


def _describe_record(record: Any) -> str:
    from .providers.native_executors import _pair

    try:
        pair = _pair(record)
    except Exception:
        pair = "?"
    return f"{record.id} {record.type} {pair}".strip()


def _load_slug_records(runtime: Any, slug: str) -> list:
    """All executor records of the slug, with live in-memory state overlaid on
    running ones (the durable log is transition-only)."""
    from .providers.native_executors import _overlay_live_record

    return [
        _overlay_live_record(r, runtime) for r in runtime.store.load_by_slug(slug)
    ]


def _nonterminal_records(runtime: Any, slug: str) -> list:
    return [r for r in _load_slug_records(runtime, slug) if r.status in _NONTERMINAL]


async def _deterministic_baseline(
    runtime: Any, slug: str, policy: ShutdownPolicy
) -> tuple[int, list[str], list[str]]:
    """Stop the slug's nonterminal executors with ``keep_position`` per policy.

    Each stop is isolated so one failure never aborts the rest. Returns
    ``(stopped_count, failures, attempted_ids)``.
    """
    running = _nonterminal_records(runtime, slug)
    stopped = 0
    failures: list[str] = []
    attempted: list[str] = []
    for record in running:
        attempted.append(record.id)
        keep = _keep_position(record, policy)
        try:
            await ops.stop(runtime, executor_id=record.id, keep_position=keep)
        except Exception as e:
            failures.append(f"stop {record.id}: {e}")
            continue
        stopped += 1
    return stopped, failures, attempted


def _stranded_records(
    records: list, policy: ShutdownPolicy, attempted_ids: set[str]
) -> list:
    """Records the policy said to close that still hold risk.

    Two ways to be stranded: still nonterminal after the stops settled, or a
    position-kind record we stopped whose state still holds venue inventory
    (e.g. a detach where the policy demanded a close).
    """
    out = []
    for r in records:
        if _keep_position(r, policy):
            continue
        if r.status in _NONTERMINAL or (r.id in attempted_ids and _holds_inventory(r)):
            out.append(r)
    return out


async def _verify_and_retry(
    runtime: Any, slug: str, policy: ShutdownPolicy, attempted_ids: set[str]
) -> list:
    """Re-read the store after stops settle; retry the close once; return the
    records still stranded.

    Never trusts the LLM: computes which records *should* be flat under the
    policy, and if any remain, stops any still-nonterminal one that should be
    closed and re-checks.
    """
    await asyncio.sleep(_SETTLE_DELAY_S)
    records = _load_slug_records(runtime, slug)
    stranded = _stranded_records(records, policy, attempted_ids)
    if not stranded:
        return []

    for r in stranded:
        if r.status not in _NONTERMINAL:
            continue
        try:
            await ops.stop(runtime, executor_id=r.id, keep_position=False)
            attempted_ids.add(r.id)
        except Exception:
            log.exception("shutdown: retry stop failed for %s", r.id)

    await asyncio.sleep(_SETTLE_DELAY_S)
    records = _load_slug_records(runtime, slug)
    return _stranded_records(records, policy, attempted_ids)


def _build_llm_context(
    policy: ShutdownPolicy,
    running: list,
    failures: list[str],
) -> str:
    """Post-baseline state handed to the LLM cleanup pass."""
    lines = [
        "An emergency shutdown was triggered. The deterministic winddown has ALREADY run.",
        f"Policy: on_kill_switch={policy.on_kill_switch}, "
        f"cancel_open_orders={policy.cancel_open_orders}.",
        "",
        f"Executors still running after the baseline stop ({len(running)}):",
    ]
    lines += [
        f"  - {_describe_record(r)} [{r.status}]" for r in running
    ] or ["  (none)"]
    if failures:
        lines += ["", f"Deterministic winddown errors ({len(failures)}):"]
        lines += [f"  - {f}" for f in failures]
    return "\n".join(lines)


async def _run_llm_cleanup(
    engine: Any,
    runtime: Any,
    policy: ShutdownPolicy,
    body: str,
    failures: list[str],
) -> None:
    """Best-effort LLM nuance pass on top of the guaranteed deterministic floor.

    Bounded by a hard 300s timeout (the same ceiling the tick ACP session runs
    under) and fully fail-open: the safety-critical winddown already happened, so
    any hang or error here is logged and swallowed — it can never strand a position
    the way an LLM-only shutdown could.
    """
    agent = getattr(engine, "agent", None)
    if not body or agent is None:
        return
    try:
        from .policies import AUTO
        from .run import run_agent
        from condor.agents.context import build_agent_context

        running = _nonterminal_records(runtime, agent.slug)
        context = _build_llm_context(policy, running, failures)
        prompt = build_agent_context(agent, body, context)
        async with asyncio.timeout(300):
            # Unattended auto-approve: the cleanup pass must be able to close
            # positions without a human in the loop (that is its whole point);
            # the deterministic floor already ran, so this can only reduce risk.
            await run_agent(
                agent,
                prompt,
                permission_policy=AUTO,
                timeout_s=300,
            )
    except asyncio.TimeoutError:
        log.warning(
            "TickEngine %s: shutdown LLM cleanup timed out (floor already secured)",
            engine.agent_id,
        )
    except Exception:
        log.exception(
            "TickEngine %s: shutdown LLM cleanup failed (floor already secured)",
            engine.agent_id,
        )


async def run_shutdown(engine: Any, reason: str) -> None:
    """Wind down this agent's executors/positions per its ``shutdown.md`` policy.

    Sequence (the LLM judgment pass is inserted between baseline and verify):

    1. Load the resolved policy + body; record ``shutdown_start``.
    2. Deterministic baseline: stop the slug's nonterminal executors with
       ``keep_position`` per policy (the guaranteed floor).
    3. Verify: re-read the store after the stops settle, retry the close once,
       and loudly alert the user if anything the policy said to close still
       holds risk.
    4. Record ``shutdown_done``.

    The caller (:meth:`TickEngine._run_shutdown`) owns the idempotency guard and
    the self-stop; this function performs the winddown itself and never raises for
    an individual stop failure -- failures are collected and surfaced.
    """
    policy, body = load_shutdown_policy(engine.agent)
    agent_id = engine.agent_id
    slug = engine.agent.slug
    log.warning(
        "TickEngine %s: SHUTDOWN starting -- %s (policy=%s)",
        agent_id,
        reason,
        policy.on_kill_switch,
    )
    engine.record_decision(
        "shutdown_start", f"{reason} (policy={policy.on_kill_switch})"
    )

    runtime = peek_executor_runtime()
    if runtime is None:
        msg = (
            f"🚨 Agent {agent_id}: emergency shutdown could NOT reach the executor "
            f"runtime — positions may be OPEN, check manually! ({reason})"
        )
        log.error(msg)
        await engine._notify(msg)
        engine.record_decision("shutdown_failed", "no executor runtime")
        return

    stopped, failures, attempted = await _deterministic_baseline(runtime, slug, policy)

    # LLM nuance pass on top of the guaranteed floor (best-effort, bounded).
    await _run_llm_cleanup(engine, runtime, policy, body, failures)

    stranded = await _verify_and_retry(runtime, slug, policy, set(attempted))

    if stranded:
        details = ", ".join(_describe_record(r) for r in stranded) or "unknown"
        msg = (
            f"🚨 Agent {agent_id}: emergency shutdown left {len(stranded)} position(s) "
            f"OPEN that the '{policy.on_kill_switch}' policy said to close: {details}. "
            f"Close them manually!"
        )
        log.error(msg)
    else:
        msg = (
            f"✅ Agent {agent_id}: emergency shutdown complete — wound down per "
            f"'{policy.on_kill_switch}' (stopped {stopped} executor(s)). ({reason})"
        )
    if failures:
        msg += f"\n⚠️ {len(failures)} winddown error(s): " + "; ".join(failures[:5])
    await engine._notify(msg)

    verified = "flat" if not stranded else f"{len(stranded)} stranded"
    engine.record_decision(
        "shutdown_done",
        f"stopped={stopped}, failures={len(failures)}, verify={verified}",
    )
