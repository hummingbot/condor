"""Who made this, when no name proves it (FEAT-106).

The index turns FEAT-105's deeds into an answer to *"whose trading is this?"*,
and every case here is a way that answer could be wrong or expensive: a chat's
deploy left unattributed, a bound specialist's work credited to Condor, a stale
record outranking an enforced rule, a name reused by a second run, an install
whose conversations hold no deeds paying to find that out — and, the one that
would be a *lie* rather than a miss, a loop session's months-old ledger dating
the log and so renaming every unrecorded chat deploy "outside Condor".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from condor import paths
from condor.agents import agent as agent_module
from condor.agents import deed_index as deed_index_module
from condor.agents import deeds
from condor.agents import strategy as strategy_module
from condor.agents.actions import DEPLOY_VERB
from condor.agents.deed_index import build_deed_index, reset_deed_index_cache
from condor.agents.fleet_map import build_fleet_map, reset_fleet_map_cache
from condor.agents.ownership import BotLedger, bot_namespace

USER = 4242


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Both halves of the walk are memoised for a minute; every test starts cold."""
    reset_fleet_map_cache()
    yield
    reset_fleet_map_cache()


@pytest.fixture(autouse=True)
def _isolated_agents(monkeypatch, tmp_path):
    """The agent registry, off the developer's install.

    ``$CONDOR_AGENTS_ROOT`` does not reach ``strategy.py``'s own ``_DATA_ROOT``
    (the suite's own fixture says so), and this module walks every session on
    disk — so without this every test here reads the real ``agents/`` tree and
    its answer depends on whose laptop it runs on.
    """
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)


def _no_engines(monkeypatch):
    """No live loops: this module is about what is on disk."""
    monkeypatch.setattr(
        "condor.runtime.loops.get_supervisor",
        lambda: type("S", (), {"all": staticmethod(dict)})(),
    )


def _write_agent(root: Path, slug: str, name: str) -> Path:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(f"---\nname: {name}\n---\n\nBody.\n")
    return d


def _write_strategy(root: Path, agent_slug: str, sslug: str) -> Path:
    """The slug comes from the frontmatter name, so the two have to agree."""
    d = root / agent_slug / "strategies" / sslug
    d.mkdir(parents=True, exist_ok=True)
    (d / "strategy.md").write_text(f"---\nname: {sslug}\n---\n\nPlaybook.\n")
    return d


def _chat_deploy(conv: str, bot: str, *, agent_slug: str = "", at: float = 0.0) -> None:
    """One deploy recorded by a chat turn, through the writer FEAT-105 ships."""
    owner = deeds.for_conversation(USER, conv, agent_slug)
    deeds.record_direct(
        owner, verb=DEPLOY_VERB, summary=f"Deploy bot {bot}", subject=bot
    )
    if at:
        _restamp(paths.conversation_dir(USER, conv), at)


def _restamp(directory: Path, at: float) -> None:
    """Move a run's whole record to an instant, so a test can order two of them."""
    import json

    ledger = directory / "owned_bots.json"
    if ledger.exists():
        data = json.loads(ledger.read_text())
        for bot in data.get("bots", {}).values():
            bot["since"] = at
            bot["last_seen"] = at
        ledger.write_text(json.dumps(data))
    log = directory / "actions.jsonl"
    if log.exists():
        rows = []
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["at"] = at
            rows.append(json.dumps(row))
        log.write_text("\n".join(rows) + "\n")
    reset_deed_index_cache()


# ── What the index can attribute ──


def test_a_chat_deploy_is_attributed_to_condor():
    """The whole point: a bot you asked for in the chat is no longer a mystery."""
    _chat_deploy("c_a1b2", "pmm-king-btcbrl")

    index = build_deed_index()
    ref = index.bots["pmm-king-btcbrl"]
    assert ref.run_key == "condor.chat"
    assert ref.run_id == "c_a1b2"
    assert ref.at > 0


def test_a_bound_specialists_deploy_is_attributed_to_that_specialist():
    """`brigado.chat`, not `condor.chat` — the agent is in the ledger, not the path."""
    _chat_deploy("c_x", "hand-rolled-bot", agent_slug="brigado")

    assert build_deed_index().bots["hand-rolled-bot"].run_key == "brigado.chat"


def test_a_delegation_and_the_dashboard_get_their_own_run_keys():
    """Three pseudo-strategies, not two: `brigado.chat` must mean one run."""
    deeds.record_direct(
        deeds.for_delegation(USER, "t_9", "brigado"),
        verb=DEPLOY_VERB,
        summary="Deploy bot deleg-bot",
        subject="deleg-bot",
    )
    deeds.record_direct(
        deeds.for_ui(USER),
        verb=DEPLOY_VERB,
        summary="Deploy bot pressed-bot",
        subject="pressed-bot",
    )

    index = build_deed_index()
    assert index.bots["deleg-bot"].run_key == "brigado.delegation"
    assert index.bots["deleg-bot"].run_id == "t_9"
    assert index.bots["pressed-bot"].run_key == "condor.ui"
    assert index.bots["pressed-bot"].run_id == "ui"


def test_a_deploy_instance_is_indexed_under_its_base():
    """`-20260731-101500` is a sibling of the name that was asked for, not a bot."""
    _chat_deploy("c_s", "chat-bot-20260731-101500")

    index = build_deed_index()
    assert "chat-bot" in index.bots
    assert index.owner_of("chat-bot-20260901-000000").run_key == "condor.chat"


def test_a_run_with_deeds_but_no_ledger_still_dates_the_log():
    """A turn that stopped a bot owns nothing — and still proves when this began."""
    deeds.record_direct(
        deeds.for_conversation(USER, "c_stop"),
        verb="manage_bots:stop",
        summary="Stop bot someone-elses-bot",
    )

    index = build_deed_index()
    assert index.bots == {}
    assert index.since > 0


def test_a_deploy_whose_ledger_write_was_lost_is_recovered_from_the_log():
    """The fallback the design names: deeds, no ledger, the row still says what."""
    _chat_deploy("c_lost", "half-written-bot")
    (paths.conversation_dir(USER, "c_lost") / "owned_bots.json").unlink()
    reset_deed_index_cache()

    assert build_deed_index().bots["half-written-bot"].run_key == "condor.chat"


def test_the_newest_deed_wins_a_reused_name():
    """A bot deleted and redeployed under the same name belongs to the second run."""
    _chat_deploy("c_old", "recycled", at=1_000_000.0)
    _chat_deploy("c_new", "recycled", at=2_000_000.0)

    ref = build_deed_index().bots["recycled"]
    assert ref.run_id == "c_new"
    assert ref.at == 2_000_000.0


# ── What the index must not do ──


def test_an_install_with_no_deeds_reads_nothing_and_judges_nothing(monkeypatch):
    """The bound on the walk: a conversation that deployed nothing costs one stat."""
    for name in ("c_1", "c_2", "c_3"):
        paths.conversation_dir(USER, name).mkdir(parents=True)

    def _boom(
        *args, **kwargs
    ):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("deed_index opened an action log it did not need")

    monkeypatch.setattr(deed_index_module, "read_actions", _boom)

    index = build_deed_index()
    assert index.bots == {}
    # Nothing recorded means nothing can be called "outside Condor" yet.
    assert index.since == 0.0


def test_a_loop_sessions_ledger_names_its_bot_but_does_not_date_the_log(
    monkeypatch, tmp_path
):
    """The lie this exists to avoid, and the attribution it exists to keep.

    A session ledger predates complete coverage by months. Its bot — deployed
    outside the namespace and never declared, which is the real shape on this
    install — is still attributable, because the ledger is a record of a deed.
    But letting it set ``since`` would rename every chat deploy of that era
    "outside Condor", so the cut is computed from FEAT-105's doors alone.
    """
    _write_agent(tmp_path, "directional_trader", "Directional Trader")
    sdir = _write_strategy(tmp_path, "directional_trader", "ema_trend_loop")
    session = sdir / "sessions" / "session_2"
    session.mkdir(parents=True)
    ledger = BotLedger(
        namespace=bot_namespace("directional_trader", "ema_trend_loop"),
        session_dir=session,
    )
    ledger.note_deploy("ema_trend_loop", now=1_000.0)
    reset_deed_index_cache()

    index = build_deed_index()
    assert index.bots["ema_trend_loop"].run_key == "directional_trader.ema_trend_loop"
    assert index.bots["ema_trend_loop"].run_id == "s2"
    assert index.since == 0.0

    _chat_deploy("c_later", "chat-bot", at=5_000.0)
    assert build_deed_index().since == 5_000.0


# ── What the map ships ──


def test_the_map_appends_pseudo_owners_that_can_never_claim_a_bot_by_name(
    monkeypatch, tmp_path
):
    """An enforced answer must never lose to an observed one, so it cannot tie.

    The pseudo-runs are ordinary owners — the tree, the URL and the bubbles all
    join on a run key already — but their namespace is empty, which is what
    keeps the prescriptive matcher from ever claiming a bot for one.

    The two *name*-based routes stay shut: an empty namespace and no declared
    bots. ``agent_ids`` is deliberately not among them (CORR-325) — a tag is an
    explicit string the run itself set, not a name it was guessed from, so
    carrying one takes nothing away from the promise in this test's title.
    """
    _no_engines(monkeypatch)
    _write_agent(tmp_path, "brigado", "Brigado")
    _chat_deploy("c_1", "some-bot", agent_slug="brigado")

    owners = {owner.run_key: owner for owner in build_fleet_map()}
    assert "brigado.chat" in owners
    pseudo = owners["brigado.chat"]
    assert pseudo.namespace == ""
    assert pseudo.declared_bots == []
    assert pseudo.agent_ids == ["brigado.chat_c_1"]
    assert pseudo.agent_name == "Brigado"
    assert pseudo.strategy_name == "Chat"


def test_a_chat_that_only_opened_an_executor_is_still_an_owner(monkeypatch, tmp_path):
    """The row that did not exist at all before CORR-325.

    ``run_keys()`` was derived from the bots the index could attribute, so a
    conversation that deployed nothing and merely opened a position produced no
    owner — and an executor carrying its tag had nowhere in the fleet map to
    hang even once the tag existed. It is now an owner like any other, carrying
    the one tag its executors can be spelled with.
    """
    _no_engines(monkeypatch)
    conv = "c_exec_only"
    deeds.record_direct(
        deeds.for_conversation(USER, conv),
        verb="create_position_executor",
        summary="Open a SOL-USDC position",
    )
    reset_deed_index_cache()

    index = build_deed_index()
    assert index.bots == {}, "nothing was deployed, so nothing is claimed by name"
    assert index.tags == {"condor.chat": [f"condor.chat_{conv}"]}

    owners = {owner.run_key: owner for owner in build_fleet_map()}
    assert owners["condor.chat"].agent_ids == [f"condor.chat_{conv}"]


def test_the_dashboards_own_deeds_never_get_a_tag(monkeypatch, tmp_path):
    """A tag names a run a model could have been told about; nobody tells a button.

    ``for_ui`` has no ref, so :func:`deeds.attribution_tag` gives it ``""``. The
    index has to agree from its side, or the fleet map would advertise
    ``condor.ui_ui`` as a ``controller_id`` that nothing could ever have set.
    """
    _no_engines(monkeypatch)
    deeds.record_direct(
        deeds.for_ui(USER), verb=DEPLOY_VERB, summary="Deploy bot ui-bot", subject="x"
    )
    reset_deed_index_cache()

    assert deeds.attribution_tag(deeds.for_ui(USER)) == ""
    assert "condor.ui" not in build_deed_index().tags


def test_a_real_strategy_is_never_shadowed_by_a_pseudo_owner(monkeypatch, tmp_path):
    """A loop session's run key is the strategy's own; the map keeps the real row."""
    _no_engines(monkeypatch)
    _write_agent(tmp_path, "brigado", "Brigado")
    sdir = _write_strategy(tmp_path, "brigado", "brl_mm")
    session = sdir / "sessions" / "session_1"
    session.mkdir(parents=True)
    BotLedger(
        namespace=bot_namespace("brigado", "brl_mm"), session_dir=session
    ).note_deploy("legacy_name", now=10.0)
    reset_fleet_map_cache()

    rows = [owner for owner in build_fleet_map() if owner.run_key == "brigado.brl_mm"]
    assert len(rows) == 1
    assert rows[0].namespace == "brigado-brl_mm"


def test_the_route_ships_the_index_beside_the_owners(monkeypatch, tmp_path):
    """One more fact through the door that already exists."""
    import asyncio

    from condor.web.routes.agents import get_fleet_map

    _no_engines(monkeypatch)
    _chat_deploy("c_1", "wire-bot")

    payload = asyncio.run(get_fleet_map(user=None))
    assert payload.deeds.bots["wire-bot"].run_key == "condor.chat"
    assert payload.deeds.since > 0
    assert any(owner.run_key == "condor.chat" for owner in payload.owners)
