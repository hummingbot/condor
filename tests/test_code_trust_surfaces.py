"""FEAT-147: the surfaces that review imported code must not be what runs it.

The two in bold in the item's own table — the ``/routines`` list page and the
source viewer — plus the brain panel the operator unmutes from, and the route
that clears the gate. Every assertion here carries the same sentinel: a probe
routine whose *module level* writes a file. If it is still absent, nothing of
that routine ran.
"""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import routines.base as base
from condor.agents import code_trust
from condor.memory.mutes import load_mutes
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=555, username="u", first_name="U", role="user")

PROBE = '''
from pathlib import Path

CATEGORY = "Probe"

Path({sentinel!r}).write_text("imported")

from pydantic import BaseModel


class Config(BaseModel):
    """Scan the pools."""

    value: int = 1


async def run(config, context):
    return "ok"
'''


@pytest.fixture
def imported_agent(tmp_path, monkeypatch):
    """A registered agent whose single routine arrived gated.

    Returns ``(slug, routine_file, sentinel)``.
    """
    from condor.agents.agent import AgentStore

    monkeypatch.setattr(base, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(base, "_routines_cache", None)
    monkeypatch.setattr(base, "_routines_mtimes", {})
    monkeypatch.setattr(base, "_path_caches", {})

    agent = AgentStore().create(name="Brigado", description="imported")
    sentinel = tmp_path / "ran"
    routines_dir = base.assistant_routines_dir(agent.slug)
    routines_dir.mkdir(parents=True, exist_ok=True)
    source = routines_dir / "lp_scanner.py"
    source.write_text(PROBE.format(sentinel=str(sentinel)))
    code_trust.arm(agent.slug)
    return agent.slug, source, sentinel


def _client(module):
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


@pytest.fixture
def routines_client():
    import condor.web.routes.routines as routines_module

    return _client(routines_module)


@pytest.fixture
def agents_client():
    import condor.web.routes.agents as agents_module

    return _client(agents_module)


# ── GET /routines ──


def test_the_list_page_shows_it_and_does_not_run_it(imported_agent, routines_client):
    slug, _, sentinel = imported_agent

    rows = {r["name"]: r for r in routines_client.get("/routines").json()}

    row = rows[f"{slug}/lp_scanner"]
    assert row["pending"] is True
    assert row["pending_reason"] == "pending"
    assert row["category"] == "Probe"
    assert row["description"] == "Scan the pools."
    assert row["fields"] == {}
    assert not sentinel.exists()


# ── GET /routines/{name}/source ──


def test_the_source_viewer_serves_the_exact_bytes_and_runs_nothing(
    imported_agent, routines_client
):
    slug, source, sentinel = imported_agent

    resp = routines_client.get(f"/routines/{slug}/lp_scanner/source")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "filename": "lp_scanner.py",
        "source": source.read_text(),
    }
    assert not sentinel.exists()


def test_the_source_viewer_still_refuses_a_path_outside_the_roots(
    imported_agent, routines_client, tmp_path, monkeypatch
):
    """The confinement CORR-585 established is unchanged by the new branch."""
    import condor.web.routes.routines as routines_module

    slug, _, _ = imported_agent
    outside = tmp_path / "elsewhere" / "lp_scanner.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("secrets\n")

    from condor.routine_store import RoutineStore

    def _store():
        store = RoutineStore()
        store._pending_all = lambda: {
            f"{slug}/{row.name}": replace(row, source_path=outside)
            for row in base.pending_routines(slug)
        }
        return store

    monkeypatch.setattr(routines_module, "get_routine_store", _store)

    resp = routines_client.get(f"/routines/{slug}/lp_scanner/source")
    assert resp.status_code == 403, resp.text
    assert "secrets" not in resp.text


# ── the brain panel ──


def test_the_panel_lists_it_pending_and_its_mute_toggle_still_works(
    imported_agent, agents_client
):
    slug, _, sentinel = imported_agent

    rows = {
        r["name"]: r
        for r in agents_client.get(f"/agents/{slug}/brain").json()["routines"]
    }
    assert rows["lp_scanner"]["pending"] is True
    assert rows["lp_scanner"]["muted"] is False
    assert rows["lp_scanner"]["description"] == "Scan the pools."

    put = agents_client.put(
        f"/agents/{slug}/mutes",
        json={"kind": "routine", "name": "lp_scanner", "muted": True},
    )
    assert put.status_code == 200, put.text
    assert load_mutes(slug)["routines"] == {"lp_scanner"}

    rows = {
        r["name"]: r
        for r in agents_client.get(f"/agents/{slug}/brain").json()["routines"]
    }
    assert rows["lp_scanner"]["muted"] is True and rows["lp_scanner"]["pending"] is True
    assert not sentinel.exists()


# ── PUT /agents/{slug}/code-trust ──


def test_allowing_makes_exactly_that_routine_loadable(
    imported_agent, agents_client, tmp_path
):
    slug, source, sentinel = imported_agent
    sibling = source.parent / "funding_watch.py"
    sibling.write_text(PROBE.format(sentinel=str(tmp_path / "sibling.ran")))

    put = agents_client.put(
        f"/agents/{slug}/code-trust",
        json={"kind": "routine", "name": "lp_scanner", "allowed": True},
    )

    assert put.status_code == 200, put.text
    assert put.json()["allowed"] is True
    assert put.json()["digest"] == code_trust.digest_of(source.read_text())
    assert set(base.assistant_routines(slug)) == {"lp_scanner"}
    assert sentinel.exists()
    assert not (tmp_path / "sibling.ran").exists()
    assert [row.name for row in base.pending_routines(slug)] == ["funding_watch"]


def test_withdrawing_consent_takes_it_back_out(imported_agent, agents_client):
    slug, source, _ = imported_agent
    agents_client.put(
        f"/agents/{slug}/code-trust",
        json={"kind": "routine", "name": "lp_scanner", "allowed": True},
    )
    assert set(base.assistant_routines(slug)) == {"lp_scanner"}

    put = agents_client.put(
        f"/agents/{slug}/code-trust",
        json={"kind": "routine", "name": "lp_scanner", "allowed": False},
    )

    assert put.status_code == 200, put.text
    assert base.assistant_routines(slug) == {}
    assert code_trust.is_armed(slug) is True


def test_an_unknown_routine_is_a_404(imported_agent, agents_client):
    slug, _, _ = imported_agent
    resp = agents_client.put(
        f"/agents/{slug}/code-trust",
        json={"kind": "routine", "name": "no_such_thing", "allowed": True},
    )
    assert resp.status_code == 404, resp.text


def test_a_traversal_name_is_refused(imported_agent, agents_client):
    slug, _, _ = imported_agent
    resp = agents_client.put(
        f"/agents/{slug}/code-trust",
        json={"kind": "routine", "name": "../../etc/passwd", "allowed": True},
    )
    assert resp.status_code == 404, resp.text


def test_only_a_routine_can_be_allowed(imported_agent, agents_client):
    slug, _, _ = imported_agent
    resp = agents_client.put(
        f"/agents/{slug}/code-trust",
        json={"kind": "skill", "name": "lp_rebalance", "allowed": True},
    )
    assert resp.status_code == 400, resp.text
    assert "only a routine" in resp.json()["detail"]


def test_an_agent_nobody_imported_is_refused(tmp_path, monkeypatch, agents_client):
    """Allowing one file here would gate every sibling as a side effect."""
    from condor.agents.agent import AgentStore

    monkeypatch.setattr(base, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(base, "_path_caches", {})
    agent = AgentStore().create(name="Homegrown", description="local")
    routines_dir = base.assistant_routines_dir(agent.slug)
    routines_dir.mkdir(parents=True, exist_ok=True)
    (routines_dir / "scan.py").write_text(PROBE.format(sentinel=str(tmp_path / "x")))

    resp = agents_client.put(
        f"/agents/{agent.slug}/code-trust",
        json={"kind": "routine", "name": "scan", "allowed": True},
    )

    assert resp.status_code == 400, resp.text
    assert "need no execution consent" in resp.json()["detail"]
    assert code_trust.is_armed(agent.slug) is False


# ── running it ──


def test_running_a_pending_routine_is_a_403_naming_the_allow_action(
    imported_agent, routines_client, monkeypatch
):
    """403, not the 404 a plain ``ValueError`` would have produced."""
    import condor.web.routes.routines as routines_module

    slug, _, sentinel = imported_agent
    monkeypatch.setattr(routines_module, "check_server_access", lambda *a, **k: None)

    resp = routines_client.post(
        "/routines/run",
        json={"routine_name": f"{slug}/lp_scanner", "server_name": "local"},
    )

    assert resp.status_code == 403, resp.text
    assert "has not been allowed to run" in resp.json()["detail"]
    assert not sentinel.exists()


# ── Deleting an imported agent's routine (#313) ──


def test_delete_routine_removes_file_consent_record_and_mute(imported_agent):
    from condor.memory.mutes import is_muted, load_mutes, set_muted
    from mcp_servers.condor.tools.routines import delete_routine

    slug, source, _ = imported_agent
    code_trust.allow(slug, "lp_scanner", source.read_text("utf-8"))
    set_muted(slug, "routine", "lp_scanner", True)

    assert source.is_file()
    assert code_trust.load(slug).allowed == {
        "lp_scanner": code_trust.digest_of(source.read_text("utf-8"))
    }
    assert is_muted(slug, "routine", "lp_scanner")

    res = delete_routine("lp_scanner", target=slug)
    assert res == {"deleted": True, "name": "lp_scanner"}

    assert not source.exists()
    assert code_trust.load(slug).allowed == {}
    assert not is_muted(slug, "routine", "lp_scanner")
    assert load_mutes(slug)["routines"] == set()
    assert "lp_scanner" not in base.assistant_routines(slug, include_muted=True)
    assert base.pending_routines(slug) == []


def test_delete_local_override_preserves_mute_when_shared_routine_exists(
    imported_agent, monkeypatch, tmp_path
):
    from condor.memory.mutes import is_muted, set_muted
    from condor.paths import stock_agents_root
    from mcp_servers.condor.tools.routines import delete_routine

    slug, source, _ = imported_agent
    shared_dir = stock_agents_root() / "_shared" / "routines"
    shared_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "backtest_chart.py").write_text(
        PROBE.format(sentinel=str(tmp_path / "shared.ran"))
    )

    local_override = source.parent / "backtest_chart.py"
    local_override.write_text(PROBE.format(sentinel=str(tmp_path / "local.ran")))
    code_trust.allow(slug, "backtest_chart", local_override.read_text("utf-8"))
    set_muted(slug, "routine", "backtest_chart", True)

    res = delete_routine("backtest_chart", target=slug)
    assert res == {"deleted": True, "name": "backtest_chart"}
    assert not local_override.exists()
    assert "backtest_chart" not in code_trust.load(slug).allowed
    assert is_muted(slug, "routine", "backtest_chart")


def test_web_delete_agent_routine_and_deletable_flag(
    imported_agent, agents_client, tmp_path
):
    from condor.memory.mutes import is_muted, set_muted
    from condor.paths import stock_agents_root

    slug, source, _ = imported_agent
    stock_dir = stock_agents_root() / slug / "routines"
    stock_dir.mkdir(parents=True, exist_ok=True)
    (stock_dir / "shipped_only.py").write_text(
        PROBE.format(sentinel=str(tmp_path / "shipped.ran"))
    )

    set_muted(slug, "routine", "lp_scanner", True)
    rows = {
        r["name"]: r
        for r in agents_client.get(f"/agents/{slug}/brain").json()["routines"]
    }
    assert rows["lp_scanner"]["deletable"] is True
    assert rows["shipped_only"]["deletable"] is False

    assert (
        agents_client.put(
            f"/agents/{slug}/code-trust",
            json={"kind": "routine", "name": "lp_scanner", "allowed": True},
        ).status_code
        == 200
    )
    rows = {
        r["name"]: r
        for r in agents_client.get(f"/agents/{slug}/brain").json()["routines"]
    }
    assert rows["lp_scanner"]["deletable"] is True

    res = agents_client.delete(f"/agents/{slug}/routines/lp_scanner")
    assert res.status_code == 200, res.text
    assert res.json() == {"deleted": True}

    assert not source.exists()
    assert code_trust.load(slug).allowed == {}
    assert not is_muted(slug, "routine", "lp_scanner")
    assert agents_client.delete(f"/agents/{slug}/routines/lp_scanner").status_code == 404
    assert (
        agents_client.delete(f"/agents/{slug}/routines/shipped_only").status_code == 400
    )
