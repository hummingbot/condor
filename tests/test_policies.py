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
