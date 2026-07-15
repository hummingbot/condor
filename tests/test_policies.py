"""Policy lattice guardrails: a human gate that cannot reach a human must
fail CLOSED (deny mutations), never degrade to auto-approve — a None
permission callback IS auto-approve in both clients."""

import asyncio

from condor.agents import policies


OPTIONS = [{"kind": "allow_once", "optionId": "ok"}]
DANGEROUS = {"tool": "manage_executors", "input": {"action": "create"}}
SAFE = {"tool": "get_market_data", "input": {}}


def test_human_gate_never_returns_none_without_chat(monkeypatch):
    gate = policies.human_gate(0)  # web consult default — no chat to confirm in
    assert gate is not None

    async def run():
        denied = await gate(DANGEROUS, OPTIONS)
        allowed = await gate(SAFE, OPTIONS)
        return denied, allowed

    denied, allowed = asyncio.run(run())
    assert denied["outcome"]["outcome"] == "cancelled"
    assert allowed["outcome"]["outcome"] == "selected"


def test_human_gate_fails_closed_without_bot(monkeypatch):
    import condor.routine_store as rs

    monkeypatch.setattr(
        rs, "get_routine_store", lambda: type("S", (), {"get_bot": lambda self: None})()
    )
    gate = policies.human_gate(12345)
    assert gate is not None  # NOT None — None would auto-approve everything

    async def run():
        return await gate(DANGEROUS, OPTIONS)

    assert asyncio.run(run())["outcome"]["outcome"] == "cancelled"


def test_deny_gate_blocks_all_dangerous_kinds():
    gate = policies.deny_gate("test reason")

    async def run():
        results = []
        for tc in (
            {"tool": "place_order", "input": {}},
            {"tool": "manage_bots", "input": {"action": "deploy"}},
            {"tool": "manage_executors", "input": {"action": "stop"}},
        ):
            results.append(await gate(tc, OPTIONS))
        return results

    for res in asyncio.run(run()):
        assert res["outcome"]["outcome"] == "cancelled"


# -- scope_gate: enforce declared tool scope for system mutations (#8) ----------

CREATE_AGENT = {"tool": "mcp__condor__manage_trading_agent",
                "input": {"action": "create_agent", "name": "x"}}
CREATE_ROUTINE = {"tool": "manage_routines", "input": {"action": "create_routine"}}
RUN_ROUTINE = {"tool": "manage_routines", "input": {"action": "run", "name": "scan"}}
WRITE_MEMORY = {"tool": "manage_memory", "input": {"action": "write"}}


def _auto_approves(call, tools):
    gate = policies.scope_gate(policies.AUTO, tools)
    return asyncio.run(gate(call, OPTIONS))["outcome"]["outcome"] == "selected"


def test_scope_gate_denies_undeclared_agent_mutation():
    # An agent that does not declare manage_trading_agent cannot create agents.
    gate = policies.scope_gate(policies.AUTO, ["manage_executors", "manage_routines"])
    out = asyncio.run(gate(CREATE_AGENT, OPTIONS))
    assert out["outcome"]["outcome"] == "cancelled"


def test_scope_gate_allows_declared_mutation():
    # Declares manage_routines -> a routine mutation is permitted (auto-approved).
    assert _auto_approves(CREATE_ROUTINE, ["manage_routines"])


def test_scope_gate_allows_reads_runs_and_memory():
    tools = ["manage_executors"]  # narrow scope
    assert _auto_approves(RUN_ROUTINE, tools)     # run is not a mutation
    assert _auto_approves(WRITE_MEMORY, tools)    # own-memory write is not a system mutation
    assert _auto_approves(SAFE, tools)


def test_scope_gate_empty_tools_is_unrestricted():
    # Empty allowlist (e.g. routine_builder) -> policy returned unwrapped (AUTO).
    assert policies.scope_gate(policies.AUTO, []) is policies.AUTO
    assert policies.scope_gate(policies.AUTO, None) is policies.AUTO


def test_scope_gate_defers_nonmutation_to_underlying_policy():
    # Underlying deny_gate cancels dangerous trades; scope_gate must defer to it
    # for non-mutation calls rather than auto-approving them.
    inner = policies.deny_gate("no human")
    gate = policies.scope_gate(inner, ["manage_executors"])
    denied = asyncio.run(gate(DANGEROUS, OPTIONS))       # manage_executors create
    assert denied["outcome"]["outcome"] == "cancelled"
