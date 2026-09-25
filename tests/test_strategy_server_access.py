"""A strategy's stored server name is not a capability (SEC-334).

``_get_client_for_strategy`` used to read ``server_name`` out of a strategy's
config.yml and hand it straight to ``cm.get_client``, so any authenticated
caller could read another install's figures through a strategy someone else
created — and kept reading them after their own access to that server had been
withdrawn. Every route that prices a strategy goes through that one function,
so the guard lives there: existence *and* reach, checked against the principal
the route resolved.

Refusal is the unpriced state the module already renders (no client, no money),
not an error. It is no longer *indistinguishable* from an offline or missing
server, though: each carries an ``unavailable`` reason and nothing else
(CORR-706).
"""

import asyncio
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from condor.agents.performance import AgentPerformance
from condor.web.routes import agents as agents_routes

ADMIN = 1
OWNER = 2  # created the strategy, may reach "srv"
STRANGER = 3  # authenticated, may NOT reach "srv"
ORPHAN = 4  # a creator id with no user record on this install (CORR-704)
SERVER = "srv"
PNL = 7.0


class _FakeCM:
    """Just the ConfigManager methods this path touches."""

    def __init__(self, servers=(SERVER,), access=((OWNER, SERVER),)):
        self.servers = set(servers)
        self.access = set(access)
        self.client = object()
        self.client_calls: list[str] = []

    def is_admin(self, user_id: int) -> bool:
        return user_id == ADMIN

    def get_user(self, user_id: int):
        return {"user_id": user_id} if user_id in (ADMIN, OWNER, STRANGER) else None

    def get_server(self, name: str):
        return {"name": name} if name in self.servers else None

    def has_server_access(self, user_id, server_name, min_permission=None) -> bool:
        # The real one answers True for an admin on any string at all, which is
        # exactly why the existence check is not redundant with this.
        return self.is_admin(user_id) or (user_id, server_name) in self.access

    async def get_client(self, name: str):
        self.client_calls.append(name)
        return self.client


@pytest.fixture()
def cm(monkeypatch):
    import config_manager

    fake = _FakeCM()
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: fake)
    monkeypatch.setattr(agents_routes, "_PERF_CACHE", {})
    monkeypatch.setattr(agents_routes, "_CLOSED_PERF_CACHE", OrderedDict())
    return fake


@pytest.fixture()
def strategy(tmp_path, monkeypatch):
    """A strategy on disk whose config.yml names ``srv``, created by OWNER."""
    home = tmp_path / "ag" / "strategies" / "st"
    (home / "sessions" / "session_1").mkdir(parents=True)
    (home / "config.yml").write_text(f"server_name: {SERVER}\n")
    strat = SimpleNamespace(
        home=home,
        slug="st",
        agent_slug="ag",
        name="st",
        description="",
        default_config={},
        default_trading_context="",
        created_by=OWNER,
    )
    monkeypatch.setattr(agents_routes, "_get_strategy", lambda slug, sslug: strat)
    monkeypatch.setattr(agents_routes, "_get_engines_for", lambda slug, sslug: [])
    return strat


@pytest.fixture()
def priced(monkeypatch):
    """The backend, if it is ever reached, reports money."""
    from condor.agents import performance as perf_mod

    async def _batch(client, ids, bot_names, failed_ids=None):
        return {
            aid: AgentPerformance(agent_id=aid, realized_pnl=PNL, total_pnl=PNL)
            for aid in ids
        }

    async def _one(*a, **kw):
        return AgentPerformance(agent_id="x", realized_pnl=PNL, total_pnl=PNL)

    async def _series(*a, **kw):
        return []

    monkeypatch.setattr(perf_mod, "fetch_agent_performance_batch", _batch)
    monkeypatch.setattr(perf_mod, "fetch_agent_performance", _one)
    monkeypatch.setattr(perf_mod, "fetch_agent_pnl_series", _series)


def _user(user_id: int):
    return SimpleNamespace(id=user_id, username=f"u{user_id}")


# ── The choke point ──


def test_a_principal_without_access_never_gets_a_client(cm, strategy):
    client, server, unavailable = asyncio.run(
        agents_routes._get_client_for_strategy(strategy.home, None, STRANGER)
    )
    assert client is None
    # Refused before the credentials are built, not after.
    assert cm.client_calls == []
    # The name is still reported: it is what the strategy declared.
    assert server == SERVER
    # And the refusal says it is one, not an empty session (CORR-706).
    assert unavailable == "no_access"


def test_a_principal_with_access_still_gets_a_client(cm, strategy):
    client, server, unavailable = asyncio.run(
        agents_routes._get_client_for_strategy(strategy.home, None, OWNER)
    )
    assert client is cm.client
    assert (server, cm.client_calls, unavailable) == (SERVER, [SERVER], "")


def test_revoking_access_stops_the_figures_of_a_strategy_already_written(cm, strategy):
    assert (
        asyncio.run(agents_routes._get_client_for_strategy(strategy.home, None, OWNER))[
            0
        ]
        is cm.client
    )
    cm.access.clear()
    assert (
        asyncio.run(agents_routes._get_client_for_strategy(strategy.home, None, OWNER))[
            0
        ]
        is None
    )


def test_a_stored_name_that_names_nothing_is_refused_even_for_an_admin(cm, strategy):
    cm.servers.clear()
    client, _, _ = asyncio.run(
        agents_routes._get_client_for_strategy(strategy.home, None, ADMIN)
    )
    assert client is None
    assert cm.client_calls == []


def test_a_strategy_that_named_no_server_is_unchanged(cm, strategy):
    (strategy.home / "config.yml").write_text("server_name: ''\n")
    assert asyncio.run(
        agents_routes._get_client_for_strategy(strategy.home, None, STRANGER)
    ) == (None, "", "no_server")


# ── The principal each route resolves ──


def test_an_admin_reading_another_users_strategy_is_held_to_its_creator(cm, strategy):
    """Not exempted: the admin bypass is not a licence over a withdrawn share."""
    assert agents_routes._strategy_principal(strategy, _user(ADMIN)) == OWNER
    cm.access.clear()
    client, _, _ = asyncio.run(
        agents_routes._get_client_for_strategy(
            strategy.home,
            None,
            agents_routes._strategy_principal(strategy, _user(ADMIN)),
        )
    )
    assert client is None


def test_a_strategy_with_no_recorded_creator_falls_back_to_the_caller(cm, strategy):
    strategy.created_by = 0
    assert agents_routes._strategy_principal(strategy, _user(ADMIN)) == ADMIN
    assert agents_routes._strategy_principal(strategy, _user(STRANGER)) == STRANGER


def test_a_creator_unknown_to_this_install_falls_back_to_the_caller(cm, strategy):
    """No user record means no subject to stand in — same as ``created_by == 0``."""
    strategy.created_by = ORPHAN
    assert agents_routes._strategy_principal(strategy, _user(ADMIN)) == ADMIN
    # A non-admin is still only ever themselves.
    assert agents_routes._strategy_principal(strategy, _user(STRANGER)) == STRANGER
    client, _, _ = asyncio.run(
        agents_routes._get_client_for_strategy(
            strategy.home,
            None,
            agents_routes._strategy_principal(strategy, _user(ADMIN)),
        )
    )
    assert client is cm.client


def test_a_non_admin_is_always_checked_as_themselves(cm, strategy):
    """Never as the creator — that would hand a stranger the creator's reach."""
    assert agents_routes._strategy_principal(strategy, _user(STRANGER)) == STRANGER


# ── The routes ──


def _detail(strategy, user_id):
    return asyncio.run(agents_routes.get_strategy("ag", "st", user=_user(user_id)))


def _perf_pnl(strategy, user_id):
    out = asyncio.run(
        agents_routes.get_strategy_performance("ag", "st", user=_user(user_id))
    )
    return out.totals["total_pnl"]


def _executors_pnl(strategy, user_id):
    out = asyncio.run(
        agents_routes.get_session_executors("ag", "st", 1, user=_user(user_id))
    )
    return out["performance"]["total_pnl"]


def _summary_pnl(strategy, user_id):
    return asyncio.run(
        agents_routes._build_strategy_summary(strategy, _user(user_id))
    ).total_pnl


@pytest.mark.parametrize(
    "read",
    [_detail, _perf_pnl, _executors_pnl, _summary_pnl],
    ids=["detail", "performance", "session-executors", "summary"],
)
def test_every_priced_route_queries_the_server_only_for_a_caller_with_access(
    cm, strategy, priced, read
):
    read(strategy, OWNER)
    assert cm.client_calls == [SERVER]

    agents_routes._PERF_CACHE.clear()
    cm.client_calls.clear()
    read(strategy, STRANGER)
    assert cm.client_calls == []


def test_the_figures_themselves_are_withheld_not_just_the_client(cm, strategy, priced):
    assert _perf_pnl(strategy, OWNER) == pytest.approx(PNL)
    assert _executors_pnl(strategy, OWNER) == pytest.approx(PNL)
    assert _summary_pnl(strategy, OWNER) == pytest.approx(PNL)

    agents_routes._PERF_CACHE.clear()
    assert _perf_pnl(strategy, STRANGER) == 0.0
    assert _executors_pnl(strategy, STRANGER) == 0.0
    assert _summary_pnl(strategy, STRANGER) == 0.0


def test_the_rollup_cache_does_not_hand_one_callers_figures_to_another(
    cm, strategy, priced
):
    """The 30s rollup cache is keyed per run — it must not be a way around this."""
    assert _perf_pnl(strategy, OWNER) == pytest.approx(PNL)
    # Same run key, still warm, different caller.
    assert _perf_pnl(strategy, STRANGER) == 0.0
    # And the allowed caller keeps their figures.
    assert _perf_pnl(strategy, OWNER) == pytest.approx(PNL)


def test_an_admin_sees_the_fleet_of_a_strategy_whose_creator_is_orphaned(
    cm, strategy, priced, monkeypatch
):
    """CORR-704: the executor visible in Bots must not vanish from Fleet."""
    from condor.agents import performance as perf_mod

    executor = {"id": "ex1", "controller_id": "ag.st_1"}

    async def _one(*a, **kw):
        return AgentPerformance(
            agent_id="x", realized_pnl=PNL, total_pnl=PNL, executors=[executor]
        )

    monkeypatch.setattr(perf_mod, "fetch_agent_performance", _one)
    strategy.created_by = ORPHAN

    out = asyncio.run(
        agents_routes.get_session_executors("ag", "st", 1, user=_user(ADMIN))
    )
    assert out["executors"] == [executor]
    assert out["performance"]["total_pnl"] == pytest.approx(PNL)
    assert _perf_pnl(strategy, ADMIN) == pytest.approx(PNL)
    assert cm.client_calls == [SERVER, SERVER]

    # A non-admin gets nothing out of the orphaned creator.
    agents_routes._PERF_CACHE.clear()
    cm.client_calls.clear()
    assert _executors_pnl(strategy, STRANGER) == 0.0
    assert _perf_pnl(strategy, STRANGER) == 0.0
    assert cm.client_calls == []


# ── Why there is no money (CORR-706) ──


def _executors(user_id):
    return asyncio.run(
        agents_routes.get_session_executors("ag", "st", 1, user=_user(user_id))
    )


def _assert_withheld(out, reason):
    assert out["unavailable"] == reason
    assert out["executors"] == []
    assert out["pnl_series"] == [] and out["deployments"] == []
    perf = out["performance"]
    assert perf["total_pnl"] == 0.0 and perf["realized_pnl"] == 0.0
    assert perf["volume"] == 0.0


def test_session_executors_says_a_refused_principal_was_refused(cm, strategy, priced):
    out = _executors(STRANGER)
    _assert_withheld(out, "no_access")
    # The reason is all a refused caller gets: no creator, no server figures.
    assert str(OWNER) not in {str(v) for v in out.values()}
    assert cm.client_calls == []


def test_session_executors_says_an_unreachable_server_is_unreachable(
    cm, strategy, priced, monkeypatch
):
    async def _offline(name):
        raise ConnectionError("server down")

    monkeypatch.setattr(cm, "get_client", _offline)
    _assert_withheld(_executors(OWNER), "unreachable")


def test_session_executors_says_a_strategy_with_no_server_has_none(
    cm, strategy, priced
):
    (strategy.home / "config.yml").write_text("server_name: ''\n")
    _assert_withheld(_executors(OWNER), "no_server")


def test_session_executors_normal_payload_has_an_empty_reason(cm, strategy, priced):
    out = _executors(OWNER)
    assert out["unavailable"] == ""
    assert set(out) == {
        "executors",
        "performance",
        "pnl_series",
        "deployments",
        "unavailable",
    }
    assert out["performance"]["total_pnl"] == pytest.approx(PNL)


def test_summary_and_detail_carry_the_reason(cm, strategy, priced):
    summary = asyncio.run(agents_routes._build_strategy_summary(strategy, _user(OWNER)))
    assert (summary.unavailable, _detail(strategy, OWNER).unavailable) == ("", "")

    agents_routes._PERF_CACHE.clear()
    summary = asyncio.run(
        agents_routes._build_strategy_summary(strategy, _user(STRANGER))
    )
    assert summary.unavailable == "no_access"
    assert summary.total_pnl == 0.0
    assert _detail(strategy, STRANGER).unavailable == "no_access"
