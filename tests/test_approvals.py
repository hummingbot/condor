"""Approvals (§4.2): durable permission events, default deny on timeout,
one-use grants (blocked call or same-run retry), idempotent resolution,
interrupted-run voiding."""

import asyncio

import pytest

from condor.agents.approvals import ApprovalManager, set_approval_manager
from condor.agents.runstore import RunStore, set_run_store


@pytest.fixture
def stores(tmp_path):
    store = RunStore(root=tmp_path)
    set_run_store(store)
    mgr = ApprovalManager()
    set_approval_manager(mgr)
    yield store, mgr
    set_approval_manager(None)


def _perm_events(store, slug, run_id):
    return [
        e for e in store.read_events(slug, run_id) if e["type"] == "permission"
    ]


def test_approve_flow_records_events_and_channel(stores):
    store, mgr = stores
    run_id = store.start_run("alpha", "consult")

    async def scenario():
        task = asyncio.create_task(
            mgr.request(
                run_id=run_id,
                agent_slug="alpha",
                summary="create order_spot 10 USDC",
                executor_id="ex1",
                timeout_s=5,
            )
        )
        await asyncio.sleep(0.05)
        open_now = mgr.open_approvals()
        assert len(open_now) == 1
        result = mgr.resolve(
            open_now[0]["approval_id"], "approve", channel="web", note="ok"
        )
        assert result["decision"] == "approved"
        return await task

    approved = asyncio.run(scenario())
    assert approved is True

    events = _perm_events(store, "alpha", run_id)
    assert events[0]["payload"]["status"] == "pending"
    assert events[0]["executor_id"] == "ex1"
    assert events[1]["payload"]["decision"] == "approved"
    assert events[1]["payload"]["channel"] == "web"


def test_deny_flow(stores):
    store, mgr = stores
    run_id = store.start_run("alpha", "consult")

    async def scenario():
        task = asyncio.create_task(
            mgr.request(
                run_id=run_id, agent_slug="alpha", summary="s", timeout_s=5
            )
        )
        await asyncio.sleep(0.05)
        mgr.resolve(mgr.open_approvals()[0]["approval_id"], "deny", channel="mcp")
        return await task

    assert asyncio.run(scenario()) is False
    assert _perm_events(store, "alpha", run_id)[-1]["payload"]["decision"] == "denied"


def test_timeout_default_deny(stores):
    store, mgr = stores
    run_id = store.start_run("alpha", "consult")

    async def scenario():
        return await mgr.request(
            run_id=run_id, agent_slug="alpha", summary="s", timeout_s=0
        )

    assert asyncio.run(scenario()) is False
    last = _perm_events(store, "alpha", run_id)[-1]
    assert last["payload"]["decision"] == "timeout_deny"


def test_double_resolve_is_noop(stores):
    store, mgr = stores
    run_id = store.start_run("alpha", "consult")

    async def scenario():
        task = asyncio.create_task(
            mgr.request(run_id=run_id, agent_slug="alpha", summary="s", timeout_s=5)
        )
        await asyncio.sleep(0.05)
        aid = mgr.open_approvals()[0]["approval_id"]
        first = mgr.resolve(aid, "approve", channel="web")
        second = mgr.resolve(aid, "deny", channel="mcp")
        await task
        return first, second

    first, second = asyncio.run(scenario())
    assert first["decision"] == "approved"
    assert second.get("already_resolved") is True
    assert second["decision"] == "approved"  # the first decision stands
    # Exactly one decision event followed the pending event.
    decisions = [
        e for e in _perm_events(store, "alpha", run_id) if e["payload"].get("decision")
    ]
    assert len(decisions) == 1


def test_unknown_approval_resolve_reports_error(stores):
    _, mgr = stores
    out = mgr.resolve("nope", "approve", channel="web")
    assert "error" in out


def test_grant_consumed_by_same_run_retry_once(stores):
    """Approval after the blocked call died: the grant is one-use and keyed
    by (run, executor_id) — the SAME create's retry consumes it, a second
    retry re-requests."""
    store, mgr = stores
    run_id = store.start_run("alpha", "consult")

    async def scenario():
        # The blocked call times out at 0s BUT is resolved 'approved' after
        # death — simulate by requesting with timeout 0 then resolving.
        await mgr.request(
            run_id=run_id, agent_slug="alpha", summary="s",
            executor_id="ex9", timeout_s=0,
        )
        # timeout_deny already recorded; craft the retry-grant case instead:
        task = asyncio.create_task(
            mgr.request(
                run_id=run_id, agent_slug="alpha", summary="s",
                executor_id="exA", timeout_s=5,
            )
        )
        await asyncio.sleep(0.05)
        aid = mgr.open_approvals()[0]["approval_id"]
        mgr.resolve(aid, "approve", channel="web")
        first = await task  # blocked call consumed the grant

        # Retry of the same executor_id: grant already consumed → must NOT
        # short-circuit; it becomes a new pending approval (times out → deny).
        second = await mgr.request(
            run_id=run_id, agent_slug="alpha", summary="s",
            executor_id="exA", timeout_s=0,
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first is True
    assert second is False


def test_unconsumed_grant_short_circuits_retry(stores):
    """resolve-after-death: nothing awaited the event, so the grant stays
    unconsumed; the same-run same-executor retry consumes it exactly once."""
    store, mgr = stores
    run_id = store.start_run("alpha", "consult")

    async def scenario():
        task = asyncio.create_task(
            mgr.request(
                run_id=run_id, agent_slug="alpha", summary="s",
                executor_id="exB", timeout_s=5,
            )
        )
        await asyncio.sleep(0.05)
        aid = mgr.open_approvals()[0]["approval_id"]
        # Simulate the blocked call dying before resolution: cancel it.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        mgr.resolve(aid, "approve", channel="web")

        # Same run + same executor_id retry → consumes the grant, no wait.
        first_retry = await mgr.request(
            run_id=run_id, agent_slug="alpha", summary="s",
            executor_id="exB", timeout_s=0,
        )
        # One use only.
        second_retry = await mgr.request(
            run_id=run_id, agent_slug="alpha", summary="s",
            executor_id="exB", timeout_s=0,
        )
        return first_retry, second_retry

    first_retry, second_retry = asyncio.run(scenario())
    assert first_retry is True
    assert second_retry is False


def test_void_run_voids_pending(stores):
    store, mgr = stores
    run_id = store.start_run("alpha", "session")

    async def scenario():
        task = asyncio.create_task(
            mgr.request(run_id=run_id, agent_slug="alpha", summary="s", timeout_s=5)
        )
        await asyncio.sleep(0.05)
        assert mgr.void_run(run_id) == 1
        return await task

    assert asyncio.run(scenario()) is False
    last = _perm_events(store, "alpha", run_id)[-1]
    assert last["payload"]["decision"] == "interrupted_void"


def test_restart_sweep_voids_pending_approval_of_active_run(tmp_path):
    """§11: process death with an active run and a pending approval →
    startup synthesizes run_ended{interrupted} and voids the approval."""
    store = RunStore(root=tmp_path)
    run_id = store.start_run("alpha", "session")
    store.emit(
        run_id,
        "permission",
        {"approval_id": "apX", "status": "pending", "summary": "create"},
        executor_id="ex1",
    )
    store._writers[run_id].close()
    store._writers.clear()  # simulate death

    fresh = RunStore(root=tmp_path)
    voided = fresh.sweep_interrupted()
    assert [v["approval_id"] for v in voided] == ["apX"]
    events = fresh.read_events("alpha", run_id)
    assert events[-1]["type"] == "run_ended"
    assert events[-1]["payload"]["status"] == "interrupted"
    assert events[-2]["payload"]["decision"] == "interrupted_void"
