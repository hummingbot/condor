"""What the delete-strategy route says when it refuses (CORR).

``StrategyStore.delete`` refuses a strategy whose ``strategy.md`` still
resolves to the shipped library — a delete would only be undone by the next
update (``layering.stock_delete_error``). That refusal is a ``ValueError``, and
unhandled it left the route as a 500, so the browser's delete dialog fell back
to its only canned line: "It may be running." A stopped strategy then reported
the one thing it certainly was not, with the real reason nowhere on screen.

The route now maps the refusal to a 400 carrying the store's own message, the
way ``delete_agent`` already does for the reserved ``condor`` agent.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents.agent import AgentStore
from condor.agents.strategy import StrategyStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A local root and a stock one, both empty, with an agent in each."""
    local, stock = tmp_path / "local", tmp_path / "stock"
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(local))
    monkeypatch.setenv("CONDOR_STOCK_AGENTS_ROOT", str(stock))
    AgentStore().create(name="Brigado", description="BRL market making")
    return local, stock


def _ship(stock, sslug: str, name: str) -> None:
    """Put a strategy in the shipped library, the way a release would."""
    home = stock / "brigado" / "strategies" / sslug
    home.mkdir(parents=True)
    (home / "strategy.md").write_text(f"---\nname: {name}\n---\n\nTick.\n")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def test_deleting_a_shipped_strategy_is_a_400_that_says_why(roots):
    """The refusal reaches the dialog instead of a bare 500."""
    _, stock = roots
    _ship(stock, "brl_mm", "BRL MM")

    res = _client().delete("/agents/brigado/strategies/brl_mm")

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "ships with Condor" in detail
    assert "running" not in detail.lower(), "the one thing it is not"


def test_a_shipped_strategy_with_local_runtime_output_is_still_refused(roots):
    """A local ``learnings.md`` beside it is not a fork of the playbook.

    This is the shape that reported "it may be running": the strategy had been
    run, so its local home existed, but ``strategy.md`` was still stock.
    """
    local, stock = roots
    _ship(stock, "brl_mm", "BRL MM")
    home = local / "brigado" / "strategies" / "brl_mm"
    home.mkdir(parents=True)
    (home / "learnings.md").write_text("# Learnings\n")

    res = _client().delete("/agents/brigado/strategies/brl_mm")

    assert res.status_code == 400
    assert home.exists(), "a refused delete removes nothing"


def test_a_local_strategy_still_deletes(roots):
    """The refusal is scoped to shipped playbooks, not to deletes at large."""
    local, _ = roots
    StrategyStore().create(agent_slug="brigado", name="Scalp")
    home = local / "brigado" / "strategies" / "scalp"
    assert home.exists()

    res = _client().delete("/agents/brigado/strategies/scalp")

    assert res.status_code == 200
    assert res.json() == {"deleted": True}
    assert not home.exists()
