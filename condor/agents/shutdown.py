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

import asyncio
import logging
from typing import Any

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


# ---------------------------------------------------------------------------
# Deterministic winddown -- the safety-critical floor
# ---------------------------------------------------------------------------
#
# This runs first and without any LLM, so a kill switch is guaranteed to act even
# when the model or market is misbehaving (the exact conditions that trigger it).
# The reliable close primitive is ``stop_executor(keep_position=False)``, which
# closes the position the executor holds. We deliberately do NOT call
# ``clear_position_held`` on live positions: it only clears *tracking* (for
# already externally-closed positions) and would hide a still-open one. Genuine
# orphan positions (open, no executor) are left to the LLM cleanup pass and, if
# they persist, the verify step raises a loud alert.


def _is_perp(connector: str) -> bool:
    """Whether a connector is a perpetual/futures market.

    Hummingbot encodes market type in the connector name (``binance`` vs
    ``binance_perpetual``). Unknown/ambiguous connectors are treated as perp so a
    kill switch errs toward *closing* leveraged risk rather than leaving it open.
    """
    c = (connector or "").lower()
    if not c:
        return True
    return "perpetual" in c or c.endswith("_perp")


def _keep_position(executor: dict, policy: ShutdownPolicy) -> bool:
    """Whether stopping ``executor`` should keep its position, per the policy."""
    if policy.on_kill_switch == POLICY_FLATTEN_ALL:
        return False
    if policy.on_kill_switch == POLICY_KEEP_ALL:
        return True
    # keep_spot_close_perp: keep spot, close perp
    return not _is_perp(executor.get("connector", ""))


def _position_connector(position: dict) -> str:
    return position.get("connector_name") or position.get("connector") or ""


def _position_pair(position: dict) -> str:
    return position.get("trading_pair") or position.get("pair") or ""


def _describe_position(position: dict) -> str:
    return f"{_position_connector(position)} {_position_pair(position)}".strip()


def _should_remain_open(position: dict, policy: ShutdownPolicy) -> bool:
    """Whether ``position`` is *expected* to still be open after a clean winddown.

    Used by the verify step: any position the policy said to close that is still
    open is a stranded position and triggers a loud alert.
    """
    if policy.on_kill_switch == POLICY_KEEP_ALL:
        return True
    if policy.on_kill_switch == POLICY_FLATTEN_ALL:
        return False
    # keep_spot_close_perp: spot should remain, perp should be gone
    return not _is_perp(_position_connector(position))


async def _get_running_executors(engine: Any, client: Any) -> list[dict]:
    """This session's running executors -- fresh if possible, else last snapshot.

    Re-runs the core providers so the winddown acts on current truth; on any
    failure it falls back to ``engine._last_skill_data`` (already scoped to this
    session's ``agent_id`` by the last tick).
    """
    try:
        results = await engine.provider_registry.run_core_providers(
            client, engine.config, agent_id=engine.agent_id
        )
        ex_result = results.get("executors")
        if ex_result is not None and "executors" in getattr(ex_result, "data", {}):
            return list(ex_result.data["executors"])
    except Exception:
        log.exception("shutdown: executor refresh failed; using last snapshot")
    return list((engine._last_skill_data or {}).get("executors", []))


async def _fetch_positions(client: Any, agent_id: str) -> list[dict]:
    """Positions summary scoped to this session (``controller_id``)."""
    try:
        result = await client.executors.get_positions_summary(
            controller_id=agent_id or None
        )
    except Exception:
        log.exception("shutdown: failed to fetch positions summary")
        return []
    positions = result.get("positions", result) if isinstance(result, dict) else result
    if not isinstance(positions, list):
        positions = [positions] if positions else []
    return [p for p in positions if isinstance(p, dict)]


async def _deterministic_baseline(
    engine: Any, client: Any, policy: ShutdownPolicy
) -> tuple[int, list[str]]:
    """Stop this session's executors with ``keep_position`` per policy.

    Each stop is isolated so one failure never aborts the rest. Returns
    ``(stopped_count, failures)``.
    """
    from condor.fetchers.executors import stop_executor

    running = await _get_running_executors(engine, client)
    stopped = 0
    failures: list[str] = []
    for ex in running:
        ex_id = ex.get("id") or ex.get("executor_id")
        if not ex_id:
            continue
        keep = _keep_position(ex, policy)
        try:
            result = await stop_executor(client, ex_id, keep_position=keep)
        except Exception as e:  # stop_executor already guards, but be defensive
            failures.append(f"stop {ex_id}: {e}")
            continue
        if isinstance(result, dict) and result.get("status") == "error":
            failures.append(f"stop {ex_id}: {result.get('message')}")
        else:
            stopped += 1
    return stopped, failures


async def _verify_and_retry(
    engine: Any, client: Any, policy: ShutdownPolicy
) -> list[dict]:
    """Re-query positions; retry the deterministic close once; return residuals.

    Never trusts the LLM: computes which positions *should* be gone under the
    policy, and if any remain, stops any still-running executor that should be
    closed and re-checks. Returns the list of positions still stranded.
    """
    positions = await _fetch_positions(client, engine.agent_id)
    stranded = [p for p in positions if not _should_remain_open(p, policy)]
    if not stranded:
        return []

    from condor.fetchers.executors import stop_executor

    running = await _get_running_executors(engine, client)
    for ex in running:
        if _keep_position(ex, policy):
            continue
        ex_id = ex.get("id") or ex.get("executor_id")
        if not ex_id:
            continue
        try:
            await stop_executor(client, ex_id, keep_position=False)
        except Exception:
            log.exception("shutdown: retry stop failed for %s", ex_id)

    positions = await _fetch_positions(client, engine.agent_id)
    return [p for p in positions if not _should_remain_open(p, policy)]


def _build_llm_context(
    policy: ShutdownPolicy,
    running: list[dict],
    positions: list[dict],
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
        f"  - {ex.get('id') or ex.get('executor_id') or '?'} "
        f"{ex.get('connector', '?')} {ex.get('pair', '')}".rstrip()
        for ex in running
    ] or ["  (none)"]
    lines += ["", f"Open positions after the baseline stop ({len(positions)}):"]
    lines += [
        f"  - {_describe_position(p)} pnl="
        f"{p.get('unrealized_pnl_quote', p.get('unrealized_pnl', '?'))}"
        for p in positions
    ] or ["  (none)"]
    if failures:
        lines += ["", f"Deterministic winddown errors ({len(failures)}):"]
        lines += [f"  - {f}" for f in failures]
    return "\n".join(lines)


async def _run_llm_cleanup(
    engine: Any,
    client: Any,
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
        from .consult import _run_agent_to_completion

        running = await _get_running_executors(engine, client)
        positions = await _fetch_positions(client, engine.agent_id)
        context = _build_llm_context(policy, running, positions, failures)
        async with asyncio.timeout(300):
            await _run_agent_to_completion(
                slug=agent.slug,
                user_id=engine.user_id,
                chat_id=engine.chat_id,
                server_name=engine.config.get("server_name"),
                task=body,
                context=context,
                permission_callback=None,  # unattended auto-approve, like DELEGATE
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
    """Wind down this session's executors/positions per its ``shutdown.md`` policy.

    Sequence (the LLM judgment pass is inserted between baseline and verify):

    1. Load the resolved policy + body; journal ``shutdown_start``.
    2. Deterministic baseline: stop this session's executors with ``keep_position``
       per policy (the guaranteed floor).
    3. Verify: re-query positions, retry the close once, and loudly alert the user
       if anything the policy said to close is still open.
    4. Journal ``shutdown_done``.

    The caller (:meth:`TickEngine._run_shutdown`) owns the idempotency guard and
    the self-stop; this function performs the winddown itself and never raises for
    an individual API failure -- failures are collected and surfaced.
    """
    policy, body = load_shutdown_policy(engine.strategy)
    agent_id = engine.agent_id
    log.warning(
        "TickEngine %s: SHUTDOWN starting -- %s (policy=%s)",
        agent_id,
        reason,
        policy.on_kill_switch,
    )
    if engine.journal:
        engine.journal.append_action(
            engine.journal.tick_count + 1,
            "shutdown_start",
            f"{reason} (policy={policy.on_kill_switch})",
        )

    client = await engine._get_client()
    if client is None:
        msg = (
            f"🚨 Agent {agent_id}: emergency shutdown could NOT reach the API — "
            f"positions may be OPEN, check manually! ({reason})"
        )
        log.error(msg)
        await engine._notify(msg)
        if engine.journal:
            engine.journal.append_action(
                engine.journal.tick_count + 1, "shutdown_failed", "no API client"
            )
            engine.journal.record_tick("shutdown failed (no client): " + reason)
        return

    stopped, failures = await _deterministic_baseline(engine, client, policy)

    # LLM nuance pass on top of the guaranteed floor (best-effort, bounded).
    await _run_llm_cleanup(engine, client, policy, body, failures)

    stranded = await _verify_and_retry(engine, client, policy)

    if stranded:
        details = ", ".join(_describe_position(p) for p in stranded) or "unknown"
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

    if engine.journal:
        verified = "flat" if not stranded else f"{len(stranded)} stranded"
        engine.journal.append_action(
            engine.journal.tick_count + 1,
            "shutdown_done",
            f"stopped={stopped}, failures={len(failures)}, verify={verified}",
        )
        engine.journal.record_tick("shutdown: " + reason)
