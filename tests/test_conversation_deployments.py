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
from condor.agents import deeds
from condor.agents.actions import ACTIONS_FILENAME
from condor.agents.deed_index import reset_deed_index_cache
from condor.agents.deeds import attribution_tag, for_conversation
from condor.runtime.context import conversation_attribution
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
        # The install knows this server, and this caller may trade on it.
        self.servers = {SERVER}
        self.reachable = {SERVER}
        self.access_checks: list[tuple[int, str]] = []

    def is_admin(self, user_id):
        return False

    def get_server(self, name):
        return {"name": name} if name in self.servers else None

    def has_server_access(self, user_id, name, min_permission=None):
        self.access_checks.append((user_id, name))
        return name in self.reachable

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


# ── Whose server it is (SEC-333) ──


def test_a_server_the_caller_cannot_reach_is_never_queried(cm, monkeypatch):
    """A stored name is not a licence to spend the install's credentials.

    Access can be revoked, or the share withdrawn, long after the conversation
    named the server — and the name itself was written from a client-supplied
    field. So the name is re-checked at read time, and an unreachable one is
    just a conversation with no priceable fleet: the record still names the
    bot, the money column is empty, and the server is never asked.
    """
    meta = _conversation()
    _deploy(meta.id, "condor-solmm")
    cm.client = object()
    cm.reachable = set()  # the share was withdrawn
    calls = _with_perf(monkeypatch, _Perf(bot_names=["condor-solmm-1"]))

    body = _client().get(f"/conversations/{meta.id}/deployments").json()

    assert [r["label"] for r in body["deployments"]] == ["condor-solmm"]
    assert body["deployments"][0]["pnl"] == 0.0
    assert cm.calls == []  # no client was ever built for that server
    assert calls == []  # and nothing was fetched from it
    # Checked against the principal the route resolved, never waved through.
    assert cm.access_checks == [(USER.id, SERVER)]


def test_a_server_the_caller_can_trade_on_is_priced_as_before(cm, monkeypatch):
    """The normal case is untouched: reach granted, fleet queried, money shown."""
    meta = _conversation()
    _deploy(meta.id, "condor-solmm")
    cm.client = object()
    _with_perf(
        monkeypatch,
        _Perf(
            bot_names=["condor-solmm-20260904-101500"],
            controllers=[
                _controller("condor-solmm-20260904-101500", "sol", pnl=7.25, volume=100)
            ],
        ),
    )

    body = _client().get(f"/conversations/{meta.id}/deployments").json()

    assert cm.access_checks == [(USER.id, SERVER)]
    assert cm.calls == [SERVER]
    assert body["deployments"][0]["pnl"] == pytest.approx(7.25)


def test_a_server_that_no_longer_exists_is_not_asked_about(cm, monkeypatch):
    """Existence *and* reach — ``has_server_access`` says yes to an admin on any
    string, so a name that survives its server must not resurrect it."""
    meta = _conversation()
    _deploy(meta.id, "condor-solmm")
    cm.client = object()
    cm.servers = set()  # the server was deleted; the conversation still names it
    _with_perf(monkeypatch, _Perf())

    body = _client().get(f"/conversations/{meta.id}/deployments").json()

    assert [r["label"] for r in body["deployments"]] == ["condor-solmm"]
    assert cm.calls == []


# ── Executors a conversation opened (CORR-325) ──


def _opened_executor(conv_id: str):
    """The one file a chat turn that opens a position leaves.

    No ``owned_bots.json``: nothing was deployed. This is the run that had no
    record of any kind before — the reason the executor half of the panel was
    unreachable rather than merely empty.
    """
    deeds.record_direct(
        deeds.for_conversation(USER.id, conv_id),
        verb="create_position_executor",
        summary="Open a SOL-USDC position",
    )
    reset_deed_index_cache()


def _executor(cid: str, *, eid: str = "e1", pnl: float = 0.0, volume: float = 0.0):
    return {
        "id": eid,
        "controller_id": cid,
        "type": "position_executor",
        "pair": "SOL-USDC",
        "connector": "binance",
        "timestamp": NOW,
        "close_timestamp": 0,
        "pnl": pnl,
        "volume": volume,
    }


def test_an_executor_a_conversation_opened_is_one_of_its_rows(cm, monkeypatch):
    """The tag the chat was handed is the tag this route asks the API with.

    Both halves of CORR-325 in one assertion: the string
    ``conversation_attribution`` puts in the prompt is
    ``deeds.attribution_tag``'s, and so is the ``agent_id`` this route joins on.
    One rule spelled once — if the two ever drift, the row disappears and this
    fails.
    """
    meta = _conversation()
    _opened_executor(meta.id)
    cm.client = object()

    tag = attribution_tag(for_conversation(USER.id, meta.id, meta.agent_slug))
    assert tag == f"condor.chat_{meta.id}"
    assert tag in conversation_attribution(tag)

    perf = _Perf()
    perf.executors = [_executor(tag, pnl=12.5, volume=900)]
    calls = _with_perf(monkeypatch, perf)

    body = _client().get(f"/conversations/{meta.id}/deployments").json()

    assert calls[0]["agent_id"] == tag
    assert [r["kind"] for r in body["deployments"]] == ["executor"]
    row = body["deployments"][0]
    assert row["label"] == "position SOL-USDC"
    assert row["detail"] == "binance"
    assert row["live"] is True
    assert row["pnl"] == pytest.approx(12.5)
    assert row["scope"] == "exec:e1"


def test_another_conversations_executor_is_not_this_ones(cm, monkeypatch):
    """The tag carries the conversation id, so two chats can never share a row."""
    mine = _conversation()
    _opened_executor(mine.id)
    theirs = _conversation()
    cm.client = object()

    mine_tag = attribution_tag(for_conversation(USER.id, mine.id, mine.agent_slug))
    theirs_tag = attribution_tag(
        for_conversation(USER.id, theirs.id, theirs.agent_slug)
    )
    assert mine_tag != theirs_tag

    perf = _Perf()
    perf.executors = [_executor(theirs_tag, eid="e9"), _executor(mine_tag, eid="e1")]
    _with_perf(monkeypatch, perf)

    body = _client().get(f"/conversations/{mine.id}/deployments").json()

    assert [r["scope"] for r in body["deployments"]] == ["exec:e1"]


def test_an_untagged_executor_belongs_to_no_conversation(cm, monkeypatch):
    """The failure this whole item is about, pinned as the behaviour it produces.

    Every executor a chat opened before CORR-325 carries ``controller_id: ""``.
    Nothing can recover who opened it, and the panel must show nothing rather
    than sweeping up every untagged position on the server.
    """
    meta = _conversation()
    _opened_executor(meta.id)
    cm.client = object()

    perf = _Perf()
    perf.executors = [_executor("", eid="orphan")]
    _with_perf(monkeypatch, perf)

    body = _client().get(f"/conversations/{meta.id}/deployments").json()

    assert body["deployments"] == []
