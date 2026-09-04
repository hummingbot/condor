"""What a conversation put into the world (FEAT-110).

The panel beside a chat answers one question — *did what I just asked for
actually happen, and what is it doing* — and there are exactly two ways for it
to answer wrongly: crediting a conversation with a bot somebody else redeployed,
and telling a conversation that predates the deed log that it deployed nothing.
Both are pinned here, along with the promise that licenses the rail badge: a
conversation that recorded nothing costs no Hummingbot API call.
"""

import json
import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.conversations as routes
from condor import paths
from condor.agents.actions import ACTIONS_FILENAME
from condor.agents.deed_index import reset_deed_index_cache
from condor.runtime.conversations import META_FILENAME, new_conversation
from condor.web.auth import get_current_user
from condor.web.models import WebUser

USER = WebUser(id=111, username="u", first_name="U", role="user")
SERVER = "brigado"

NOW = time.time()
LONG_AGO = NOW - 90 * 86400


class FakeConfigManager:
    """A server that answers, and a record of whether anybody asked."""

    def __init__(self, client=None):
        self.client = client
        self.calls: list[str] = []

    def is_admin(self, user_id):
        return False

    async def get_client(self, name):
        self.calls.append(name)
        return self.client


@pytest.fixture
def cm(monkeypatch):
    manager = FakeConfigManager()
    monkeypatch.setattr(routes, "get_config_manager", lambda: manager)
    reset_deed_index_cache()
    yield manager
    reset_deed_index_cache()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


def _conversation(*, server: str | None = SERVER, updated_at: float | None = None):
    meta = new_conversation(USER.id, "web", server_name=server)
    if updated_at is not None:
        # Straight into the file: ``update_meta`` goes through ``write_status``,
        # which stamps ``updated_at`` with the wall clock whatever it is handed.
        path = paths.conversation_dir(USER.id, meta.id) / META_FILENAME
        raw = json.loads(path.read_text())
        raw["updated_at"] = updated_at
        path.write_text(json.dumps(raw))
    return meta


def _deploy(conv_id: str, base: str, *, since: float = NOW, at: float | None = None):
    """The two files a chat turn that deploys a bot leaves (FEAT-105)."""
    directory = paths.conversation_dir(USER.id, conv_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "owned_bots.json").write_text(
        json.dumps(
            {
                "namespace": "condor-chat",
                "bots": {
                    base: {
                        "base": base,
                        "origin": "deployed",
                        "since": since,
                        "last_seen": since,
                    }
                },
            }
        )
    )
    (directory / ACTIONS_FILENAME).write_text(
        json.dumps(
            {
                "tick": 0,
                "at": at if at is not None else since,
                "tool": "manage_bots",
                "verb": "manage_bots:deploy",
                "summary": f"Deploy bot {base} (1 controller)",
                "ok": True,
                "error": "",
                "subject": base,
            }
        )
        + "\n"
    )
    reset_deed_index_cache()
    return directory


class _Perf:
    """The fields of ``AgentPerformance`` the ledger reads, and nothing else."""

    def __init__(self, *, bot_names=(), controllers=()):
        self.bot_names = list(bot_names)
        self.bot_instances = list(bot_names)
        self.controllers = list(controllers)
        self.executors = []


def _controller(bot_name, cid, pnl=0.0, volume=0.0):
    return {
        "bot_name": bot_name,
        "controller_id": cid,
        "controller_name": "pmm_simple",
        "connector": "binance",
        "trading_pair": "SOL-USDC",
        "realized_pnl_quote": pnl,
        "unrealized_pnl_quote": 0.0,
        "volume_traded": volume,
    }


def _with_perf(monkeypatch, perf):
    """Stand in for the one Hummingbot call this route makes, and count it."""
    calls: list[dict] = []

    async def fake(client, agent_id, bot_names=None, since=0.0):
        calls.append({"agent_id": agent_id, "bot_names": list(bot_names or [])})
        return perf

    monkeypatch.setattr(
        "condor.agents.performance.fetch_agent_performance", fake, raising=True
    )
    return calls


# ── A conversation that deployed something ──


def test_a_deployed_bot_is_a_row_with_its_controllers_and_its_money(cm, monkeypatch):
    meta = _conversation()
    _deploy(meta.id, "condor-solmm")
    cm.client = object()
    _with_perf(
        monkeypatch,
        _Perf(
            bot_names=["condor-solmm-20260904-101500"],
            controllers=[
                _controller("condor-solmm-20260904-101500", "sol", pnl=12.5, volume=900)
            ],
        ),
    )

    body = _client().get(f"/conversations/{meta.id}/deployments").json()

    kinds = [r["kind"] for r in body["deployments"]]
    assert kinds == ["bot", "controller"]
    bot, controller = body["deployments"]
    assert bot["label"] == "condor-solmm"
    assert bot["live"] is True
    assert bot["pnl"] == pytest.approx(12.5)
    assert bot["scope"] == "bot:condor-solmm-20260904-101500"
    assert controller["label"] == "sol"
    assert controller["detail"] == "binance · SOL-USDC"
    assert body["predates_ledger"] is False


def test_the_bot_is_named_even_when_the_server_cannot_be_priced(cm, monkeypatch):
    """An offline server costs the money column, never the record."""
    meta = _conversation()
    _deploy(meta.id, "condor-solmm")
    cm.client = None  # get_client answered with nothing

    body = _client().get(f"/conversations/{meta.id}/deployments").json()

    assert [r["label"] for r in body["deployments"]] == ["condor-solmm"]
    assert body["deployments"][0]["pnl"] == 0.0


def test_a_base_another_conversation_redeployed_is_no_longer_this_ones(cm, monkeypatch):
    """Liveness is the newest claim, so two chats never bill the same bot."""
    mine = _conversation()
    _deploy(mine.id, "condor-solmm", since=NOW - 3600)
    theirs = _conversation()
    _deploy(theirs.id, "condor-solmm", since=NOW)
    cm.client = object()
    calls = _with_perf(monkeypatch, _Perf())

    body = _client().get(f"/conversations/{mine.id}/deployments").json()

    assert [r["live"] for r in body["deployments"]] == [False]
    # And the bot is not priced against this conversation either.
    assert calls[0]["bot_names"] == []


def test_the_executor_tag_names_this_conversation_and_never_the_empty_one(
    cm, monkeypatch
):
    """`controller_id: ""` is every unattributed executor on the install."""
    meta = _conversation()
    _deploy(meta.id, "condor-solmm")
    cm.client = object()
    calls = _with_perf(monkeypatch, _Perf(bot_names=["condor-solmm-1"]))

    _client().get(f"/conversations/{meta.id}/deployments")

    assert calls[0]["agent_id"] == f"condor.chat_{meta.id}"


# ── A conversation that deployed nothing ──


def test_a_conversation_with_no_record_returns_no_rows_and_asks_nobody(cm):
    meta = _conversation()

    body = _client().get(f"/conversations/{meta.id}/deployments").json()

    assert body["deployments"] == []
    # The promise that licenses badging this on the rail: no record, no fetch.
    assert cm.calls == []


def test_deployed_nothing_and_predates_the_ledger_are_different_answers(cm):
    """The one distinction the panel's two empty states are made of."""
    recorded = _conversation()
    _deploy(recorded.id, "condor-solmm", since=NOW)
    silent = _conversation()
    old = _conversation(updated_at=LONG_AGO)

    answers = _client()
    assert answers.get(f"/conversations/{silent.id}/deployments").json() == {
        "deployments": [],
        "predates_ledger": False,
    }
    assert answers.get(f"/conversations/{old.id}/deployments").json() == {
        "deployments": [],
        "predates_ledger": True,
    }


def test_nothing_predates_a_ledger_that_has_never_been_written(cm):
    """No deed anywhere means no cut, and no cut judges nothing (FEAT-106)."""
    old = _conversation(updated_at=LONG_AGO)

    body = _client().get(f"/conversations/{old.id}/deployments").json()

    assert body["predates_ledger"] is False


# ── Whose conversation it is ──


def test_someone_elses_conversation_is_not_yours_to_read(cm):
    meta = new_conversation(222, "web", server_name=SERVER)

    assert _client().get(f"/conversations/{meta.id}/deployments").status_code == 404


def test_an_unknown_conversation_is_a_404(cm):
    assert _client().get("/conversations/nope/deployments").status_code == 404
