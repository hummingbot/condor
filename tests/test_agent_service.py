"""Phase 2 acceptance: AgentService tombstone-delete semantics (§5.2)."""

import pytest

import condor.agents.agent as agent_mod
from condor.agents.lifecycle import LifecycleError
from condor.agents.service import AgentService


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "_DATA_ROOT", tmp_path)
    # service.py resolves the tombstone path through agents_data_root()
    monkeypatch.setattr("condor.agents.service.agents_data_root", lambda: tmp_path)
    return AgentService()


def _create(svc, name="Tomb Test"):
    return svc.create(
        name=name,
        instructions="body",
        # A declared non-trading scope — no risk baseline required.
        tools=["manage_routines"],
        default_config={},
    )


def test_partial_config_patch_merges_not_replaces(svc):
    """A partial default_config/risk_limits patch preserves the other keys —
    editing one param must not silently strip the rest of the strategy block
    (the corruption a wholesale setattr caused)."""
    agent = svc.create(
        name="Merge Test",
        instructions="body",
        tools=["manage_executors"],
        denomination="SOL",
        risk_limits={"max_position_size_quote": 0.1, "max_open_executors": 3},
        default_config={
            "frequency_sec": 60,
            "take_profit_pct": 0.03,
            "stop_loss_pct": 0.05,
            "entry_guards": {"stop_loss_cooldown": {"hours": 4}},
        },
    )

    # Change ONE nested key via a partial patch.
    updated = svc.update(agent.slug, {"default_config": {"take_profit_pct": 0.05}})
    dc = updated.default_config
    assert dc["take_profit_pct"] == 0.05  # changed
    assert dc["frequency_sec"] == 60  # preserved
    assert dc["stop_loss_pct"] == 0.05  # preserved
    assert dc["entry_guards"] == {"stop_loss_cooldown": {"hours": 4}}  # preserved

    # Same for the top-level risk baseline.
    updated = svc.update(agent.slug, {"risk_limits": {"max_open_executors": 5}})
    assert updated.risk_limits["max_open_executors"] == 5  # changed
    assert updated.risk_limits["max_position_size_quote"] == 0.1  # preserved

    # Reload from disk to confirm the merge persisted, not just the in-memory obj.
    reloaded = svc.get(agent.slug)
    assert reloaded.default_config["frequency_sec"] == 60
    assert reloaded.risk_limits["max_position_size_quote"] == 0.1


def test_delete_tombstones_and_reserves_slug(svc):
    agent = _create(svc)
    slug = agent.slug

    result = svc.delete(slug)
    assert result["tombstoned"] is True
    assert svc.is_tombstoned(slug)

    # History readable: AGENT.md still on disk, get() still returns it.
    assert svc.get(slug).slug == slug
    # …but hidden from the default list.
    assert slug not in [a.slug for a in svc.list()]
    assert slug in [a.slug for a in svc.list(include_tombstoned=True)]

    # Slug reserved: recreate is rejected.
    with pytest.raises(LifecycleError) as ei:
        _create(svc)
    assert ei.value.status == 409
    assert "reserved" in ei.value.message


@pytest.mark.asyncio
async def test_tombstoned_agent_cannot_launch_or_edit(svc):
    agent = _create(svc)
    svc.delete(agent.slug)

    with pytest.raises(LifecycleError) as ei:
        await svc.run(agent.slug)
    assert ei.value.status == 409

    with pytest.raises(LifecycleError):
        svc.update(agent.slug, {"description": "new"})

    with pytest.raises(LifecycleError):
        await svc.consult(agent.slug, "hi")


def test_delete_rejected_while_running(svc, monkeypatch):
    created = _create(svc)

    from types import SimpleNamespace

    fake_engine = SimpleNamespace(
        agent_id=f"{created.slug}_1",
        is_running=True,
        agent=SimpleNamespace(slug=created.slug),
    )
    monkeypatch.setattr(
        "condor.agents.engine.get_all_engines",
        lambda: {fake_engine.agent_id: fake_engine},
    )
    agent = created
    with pytest.raises(LifecycleError) as ei:
        svc.delete(agent.slug)
    assert ei.value.status == 409
    assert "running" in ei.value.message


def test_delete_rejected_with_nonterminal_executors(svc, monkeypatch):
    agent = _create(svc)
    monkeypatch.setattr(
        AgentService,
        "_nonterminal_executors",
        staticmethod(lambda slug: ["e1", "e2"]),
    )
    with pytest.raises(LifecycleError) as ei:
        svc.delete(agent.slug)
    assert ei.value.status == 409
    assert "nonterminal" in ei.value.message


def test_terminal_filled_inventory_blocks_delete(svc, monkeypatch):
    from types import SimpleNamespace

    agent = _create(svc)
    record = SimpleNamespace(
        id="filled-order",
        status="CLOSED",
        type="order_spot",
        config={"base_token": "WIF"},
        state={
            "orders": [
                {
                    "venue_order_id": "v1",
                    "role": "entry",
                    "side": "buy",
                    "requested_qty": "1",
                    "requested_unit": "base",
                    "cumulative_filled_base_qty": "1",
                    "cumulative_filled_quote_qty": "10",
                    "fees_by_asset": {},
                    "status": "FILLED",
                }
            ]
        },
    )
    runtime = SimpleNamespace(store=SimpleNamespace(load_by_slug=lambda slug: [record]))
    monkeypatch.setattr(
        "condor.executors.service.peek_executor_runtime", lambda: runtime
    )
    with pytest.raises(LifecycleError, match="inventory-bearing"):
        svc.delete(agent.slug)


def test_update_rejects_unknown_fields(svc):
    agent = _create(svc)
    with pytest.raises(LifecycleError) as ei:
        svc.update(agent.slug, {"created_by": 42})
    assert ei.value.status == 422
