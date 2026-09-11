"""A claimed bot can be handed back, from the page that claimed it.

``claim-bot`` was one-way, so a bot attached to the wrong strategy stayed there:
the claimed window is sliced into that strategy's PnL, and the repair was hand
-editing ``owned_bots.json`` under ``.condor/``. This is the undo, and the three
things asserted here are the three ways the obvious implementation of it is
quietly wrong:

1. it must clear **every** session, because ownership is re-derived on each boot
   from the whole lineage (that half is covered in ``test_bot_ownership``);
2. it must correct the ledger a **running loop** holds in memory, or the next
   thing that loop records rewrites the file and restores what was deleted;
3. a later claim must still work, which means lifting the standing "not ours"
   rather than leaving the bot permanently unclaimable.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from condor.agents.agent import AgentStore
from condor.agents.ownership import BotLedger, read_disowned
from condor.agents.strategy import StrategyStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes import agents as routes

USER = WebUser(id=555, username="u", first_name="U", role="user")

NS = "brigado-pmm_king"
BOT = "pmm-fleet-btcbrl"


class FakeConfigManager:
    def is_admin(self, user_id):
        return False

    def has_server_access(self, user_id, server_name, *a, **k):
        return True


@pytest.fixture
def strategy(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "config_manager.get_config_manager", lambda: FakeConfigManager()
    )
    monkeypatch.setattr(
        "condor.web.auth.get_config_manager", lambda: FakeConfigManager()
    )
    AgentStore().create(name="Brigado", description="BRL market making")
    return StrategyStore().create(agent_slug="brigado", name="PMM King")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _sessions(strategy):
    return strategy.home / "sessions"


def _claimed(strategy, *session_names):
    """Put the bot on this strategy's book the way the claim route does."""
    for name in session_names:
        BotLedger(NS, _sessions(strategy) / name, declared=[BOT], enforced=False).adopt(
            f"{BOT}-20260902-101324", now=1000.0
        )


def test_unassigning_clears_the_whole_lineage(strategy, client):
    _claimed(strategy, "session_1", "session_2")

    res = client.post(
        "/agents/brigado/strategies/pmm_king/unclaim-bot",
        json={"bot_name": f"{BOT}-20260902-101324"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["unclaimed"] == BOT
    assert body["sessions"] == ["session_1", "session_2"]
    for name in ("session_1", "session_2"):
        assert BotLedger(NS, _sessions(strategy) / name).bases() == []
    assert read_disowned(strategy.home) == {BOT}


def test_a_running_loop_does_not_write_the_claim_back(strategy, client, monkeypatch):
    """The clobber that makes a disk-only repair look done and not be.

    A tick engine holds its ledger in memory and ``_save`` rewrites the whole
    file, so an unassign that only touched disk is undone by the next thing the
    loop records — a deploy, a refusal, the next adoption pass.
    """
    _claimed(strategy, "session_1")
    live = BotLedger(NS, _sessions(strategy) / "session_1", enforced=False)
    assert live.bases() == [BOT], "the running loop starts holding the claim"

    class FakeEngine:
        ledger = live
        session_dir = _sessions(strategy) / "session_1"

    monkeypatch.setattr(routes, "_get_engines_for", lambda *a: [FakeEngine()])

    res = client.post(
        "/agents/brigado/strategies/pmm_king/unclaim-bot",
        json={"bot_name": BOT},
    )

    assert res.json()["live_runs"] == 1
    assert live.bases() == [], "corrected in memory, not only on disk"

    # The proof: whatever the loop records next rewrites the file, and the bot
    # does not come back with it.
    live.note_deploy(f"{NS}-something-else")
    assert BotLedger(NS, _sessions(strategy) / "session_1").bases() == [
        f"{NS}-something-else"
    ]


def test_claiming_again_after_an_unassign_still_works(strategy, client):
    """The disown is a standing decision, and a claim is the same authority."""
    _claimed(strategy, "session_1")
    client.post(
        "/agents/brigado/strategies/pmm_king/unclaim-bot", json={"bot_name": BOT}
    )

    res = client.post(
        "/agents/brigado/strategies/pmm_king/claim-bot",
        json={"bot_name": BOT, "since": 1000.0},
    )

    assert res.status_code == 200
    assert res.json()["owned"] == [BOT]
    # Lifted, so the next boot's adoption pass does not skip it forever.
    assert read_disowned(strategy.home) == set()


def test_an_empty_name_is_refused(strategy, client):
    res = client.post(
        "/agents/brigado/strategies/pmm_king/unclaim-bot", json={"bot_name": "  "}
    )

    assert res.status_code == 400


def test_unassigning_under_an_unknown_strategy_is_404(strategy, client):
    res = client.post(
        "/agents/brigado/strategies/nope/unclaim-bot", json={"bot_name": BOT}
    )

    assert res.status_code == 404
