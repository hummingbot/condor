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


def test_update_rejects_unknown_fields(svc):
    agent = _create(svc)
    with pytest.raises(LifecycleError) as ei:
        svc.update(agent.slug, {"created_by": 42})
    assert ei.value.status == 422
