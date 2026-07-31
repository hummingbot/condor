"""Session-scoped bot ownership (FEAT-017).

The agent's namespace ``{agent_slug}-{strategy_slug}`` is the ownership proof, and
the permission callback is where it is enforced. These tests pin the naming
property the whole scheme rests on, the ledger's persistence, and the guard's
behavior at the tool call.
"""

import asyncio
import json

from condor.agents.ownership import (
    BotLedger,
    bot_namespace,
    declared_names,
    in_namespace,
    resolve_bot_name,
    strip_deploy_suffix,
)
from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)
from condor.agents.strategy import _slugify

_OPTIONS = [{"kind": "allow_once", "optionId": "allow"}]
NS = "brigado-ema_trend"


def _bot_call(action: str, bot_name: str, cap: float = 100.0) -> dict:
    return {
        "tool": "manage_bots",
        "input": {
            "action": action,
            "bot_name": bot_name,
            "max_global_drawdown_quote": cap,
        },
    }


def _drive(callback, call) -> str:
    return asyncio.run(callback(call, _OPTIONS))["outcome"]["outcome"]


# ---------------------------------------------------------------------------
# Namespace matching
# ---------------------------------------------------------------------------


def test_slugs_never_contain_the_delimiter():
    """The whole scheme rests on '-' being unambiguous: _slugify maps it to '_'."""
    for name in ("RIVER Scalper v2", "ema-trend", "  spot - perp  ", "BTC/USD mm"):
        assert "-" not in _slugify(name)


def test_namespace_matches_base_tag_and_deployed_instance():
    assert bot_namespace("brigado", "ema_trend") == NS
    assert in_namespace(NS, NS)
    assert in_namespace(f"{NS}-btc", NS)
    assert in_namespace(f"{NS}-btc-20260731-101500", NS)


def test_namespace_rejects_foreign_and_partial_names():
    assert not in_namespace("ema_trend_loop", NS)
    assert not in_namespace("brigado-ema_trendy", NS)  # no delimiter → different bot
    assert not in_namespace("other-ema_trend", NS)
    assert not in_namespace("", NS)


def test_strip_deploy_suffix_only_drops_the_timestamp():
    assert strip_deploy_suffix(f"{NS}-btc-20260731-101500") == f"{NS}-btc"
    assert strip_deploy_suffix(f"{NS}-btc") == f"{NS}-btc"
    assert strip_deploy_suffix(f"{NS}-2026") == f"{NS}-2026"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def test_ledger_records_deploy_and_persists(tmp_path):
    ledger = BotLedger(NS, tmp_path)
    ledger.note_deploy(f"{NS}-btc", now=1000.0)

    data = json.loads((tmp_path / "owned_bots.json").read_text())
    assert data["namespace"] == NS
    assert data["bots"][f"{NS}-btc"]["origin"] == "deployed"
    assert data["bots"][f"{NS}-btc"]["since"] == 1000.0


def test_read_owned_reads_the_ledger_without_instantiating_it(tmp_path):
    """The read-only view PnL attribution (FEAT-018) uses, oldest takeover first."""
    from condor.agents.ownership import read_owned

    ledger = BotLedger(NS, tmp_path)
    ledger.note_deploy(f"{NS}-eth", now=2000.0)
    ledger.note_deploy(f"{NS}-btc", now=1000.0)

    owned = read_owned(tmp_path)
    assert [(b.base, b.since) for b in owned] == [
        (f"{NS}-btc", 1000.0),
        (f"{NS}-eth", 2000.0),
    ]


def test_read_owned_is_empty_without_a_ledger(tmp_path):
    """A session predating the ledger reads as 'no evidence', not as an error."""
    from condor.agents.ownership import read_owned

    assert read_owned(tmp_path) == []
    assert read_owned(None) == []
    (tmp_path / "owned_bots.json").write_text("{not json")
    assert read_owned(tmp_path) == []


def test_ledger_round_trips_from_disk(tmp_path):
    first = BotLedger(NS, tmp_path)
    first.note_deploy(f"{NS}-btc", now=1000.0)
    first.note_violation("foreign-bot", "deploy", now=1001.0)

    reloaded = BotLedger(NS, tmp_path)
    assert reloaded.bases() == [f"{NS}-btc"]
    assert reloaded.owned()[0].origin == "deployed"
    assert reloaded.violations[0]["name"] == "foreign-bot"
    # Violations already on disk are history, not pending journal work.
    assert reloaded.drain_violations() == []


def test_deploy_records_the_base_not_the_instance(tmp_path):
    ledger = BotLedger(NS, tmp_path)
    ledger.adopt(f"{NS}-btc-20260731-101500", now=1000.0)
    assert ledger.bases() == [f"{NS}-btc"]
    assert ledger.owned()[0].origin == "adopted"


def test_adoption_does_not_downgrade_a_deployed_bot(tmp_path):
    ledger = BotLedger(NS, tmp_path)
    ledger.note_deploy(f"{NS}-btc", now=1000.0)
    ledger.adopt(f"{NS}-btc-20260731-101500", now=2000.0)

    owned = ledger.owned()[0]
    assert owned.origin == "deployed"
    assert owned.since == 1000.0
    assert owned.last_seen == 2000.0


def test_second_session_adopts_with_a_later_since(tmp_path):
    """A restart mints a new session; it takes over the live bot, not a duplicate."""
    s1 = tmp_path / "session_1"
    s2 = tmp_path / "session_2"
    s1.mkdir()
    s2.mkdir()

    first = BotLedger(NS, s1)
    first.note_deploy(f"{NS}-btc", now=1000.0)

    second = BotLedger(NS, s2)
    second.adopt(f"{NS}-btc-20260731-101500", now=5000.0)

    assert second.owned()[0].origin == "adopted"
    assert second.owned()[0].since > first.owned()[0].since


def test_declared_legacy_name_is_owned(tmp_path):
    ledger = BotLedger(NS, tmp_path, declared=["river-scalper"])
    assert ledger.owns("river-scalper")
    assert ledger.owns("river-scalper-20260731-101500")
    assert not ledger.owns("someone-elses-bot")


def test_declared_names_only_covers_out_of_namespace_config():
    assert declared_names({"bot_name": "river-scalper"}, NS) == ["river-scalper"]
    assert declared_names({"bot_name": f"{NS}-btc"}, NS) == []
    assert declared_names({}, NS) == []


def test_experiment_ledger_is_memory_only(tmp_path):
    ledger = BotLedger(NS, None)
    ledger.note_deploy(f"{NS}-btc", now=1000.0)
    ledger.note_violation("foreign", "deploy", now=1000.0)

    assert ledger.bases() == [f"{NS}-btc"]
    assert ledger.path is None
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# bot_mode resolution
# ---------------------------------------------------------------------------


def test_bot_mode_auto_reproduces_current_behavior():
    assert resolve_bot_name({}, "brigado", "ema_trend") == ""
    assert resolve_bot_name({"bot_name": "river-scalper"}, "brigado", "ema_trend") == (
        "river-scalper"
    )


def test_bot_mode_bot_derives_the_namespace():
    cfg = {"bot_mode": "bot", "bot_name": ""}
    assert resolve_bot_name(cfg, "brigado", "ema_trend") == NS
    # An explicit name still wins.
    cfg = {"bot_mode": "bot", "bot_name": "river-scalper"}
    assert resolve_bot_name(cfg, "brigado", "ema_trend") == "river-scalper"


def test_bot_mode_executors_forces_executor_mode():
    cfg = {"bot_mode": "executors", "bot_name": "river-scalper"}
    assert resolve_bot_name(cfg, "brigado", "ema_trend") == ""


# ---------------------------------------------------------------------------
# Enforcement at the permission callback
# ---------------------------------------------------------------------------


def _callback(ledger, **kw):
    return auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_position_size_quote=500.0)),
        RiskState(),
        ledger=ledger,
        **kw,
    )


def test_out_of_namespace_deploy_is_cancelled_and_recorded(tmp_path):
    ledger = BotLedger(NS, tmp_path)
    outcome = _drive(_callback(ledger), _bot_call("deploy", "ema_trend_loop"))

    assert outcome == "cancelled"
    assert ledger.bases() == []
    assert ledger.violations[-1] == {
        "name": "ema_trend_loop",
        "action": "deploy",
        "at": ledger.violations[-1]["at"],
    }
    assert ledger.drain_violations()  # the engine journals it next tick


def test_in_namespace_deploy_is_allowed_and_recorded(tmp_path):
    ledger = BotLedger(NS, tmp_path)
    outcome = _drive(_callback(ledger), _bot_call("deploy", f"{NS}-btc"))

    assert outcome == "selected"
    assert ledger.bases() == [f"{NS}-btc"]
    assert ledger.owned()[0].origin == "deployed"


def test_declared_legacy_name_deploy_is_allowed(tmp_path):
    ledger = BotLedger(NS, tmp_path, declared=["river-scalper"])
    assert _drive(_callback(ledger), _bot_call("deploy", "river-scalper")) == "selected"


def test_foreign_mutations_are_cancelled_too(tmp_path):
    ledger = BotLedger(NS, tmp_path)
    for action in (
        "stop_bot",
        "start_controllers",
        "stop_controllers",
        "update_config",
    ):
        call = {
            "tool": "manage_bots",
            "input": {"action": action, "bot_name": "foreign"},
        }
        assert _drive(_callback(ledger), call) == "cancelled"
    assert len(ledger.violations) == 4


def test_read_only_actions_see_the_whole_fleet(tmp_path):
    ledger = BotLedger(NS, tmp_path)
    for action in ("status", "logs", "get_config"):
        call = {
            "tool": "manage_bots",
            "input": {"action": action, "bot_name": "foreign"},
        }
        assert _drive(_callback(ledger), call) == "selected"
    assert ledger.violations == []


def test_risk_rejected_deploy_is_not_recorded_as_owned(tmp_path):
    """A deploy without a loss cap is blocked by risk — it never entered the ledger."""
    ledger = BotLedger(NS, tmp_path)
    call = {
        "tool": "manage_bots",
        "input": {"action": "deploy", "bot_name": f"{NS}-btc"},
    }

    assert _drive(_callback(ledger), call) == "cancelled"
    assert ledger.bases() == []


def test_no_ledger_changes_nothing():
    """Consults, delegations and executor-mode runs keep today's behavior exactly."""
    callback = _callback(None)
    assert _drive(callback, _bot_call("deploy", "any-name-at-all")) == "selected"


def test_dry_run_blocks_before_ownership_ever_applies(tmp_path):
    ledger = BotLedger(NS, tmp_path)
    callback = _callback(ledger, execution_mode="dry_run")

    assert _drive(callback, _bot_call("deploy", f"{NS}-btc")) == "cancelled"
    assert ledger.bases() == []
    assert ledger.violations == []


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def test_controller_block_states_the_namespace_rule(tmp_path):
    from condor.agents.prompts import _build_controller_mode_section

    ledger = BotLedger(NS, tmp_path)
    ledger.note_deploy(f"{NS}-btc", now=1000.0)
    ledger.note_violation("ema_trend_loop", "deploy", now=1001.0)

    block = _build_controller_mode_section(NS, ledger)
    assert f"'{NS}-<tag>'" in block
    assert "REFUSED" in block
    assert f"{NS}-btc (deployed)" in block
    assert "ema_trend_loop" in block


def test_controller_block_without_ledger_is_the_plain_statement():
    from condor.agents.prompts import _build_controller_mode_section

    block = _build_controller_mode_section("river-scalper", None)
    assert "[CONTROLLER MODE]" in block
    assert "river-scalper" in block
    assert "OWNERSHIP" not in block


# ---------------------------------------------------------------------------
# Engine wiring
# ---------------------------------------------------------------------------


def _engine(tmp_path, monkeypatch, config):
    from condor.agents import strategy as strategy_module
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    strategy = Strategy(agent_slug="brigado", name="EMA Trend")
    strategy.dir.mkdir(parents=True, exist_ok=True)
    agent = Agent(slug="brigado", name="Brigado")
    return TickEngine(
        agent=agent, strategy=strategy, config=dict(config), chat_id=1, user_id=1
    )


def test_engine_builds_a_ledger_in_controller_mode(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, {"bot_name": f"{NS}-btc"})

    assert engine.ledger is not None
    assert engine.ledger.namespace == NS
    assert engine.ledger.path == engine.session_dir / "owned_bots.json"


def test_engine_executor_mode_has_no_ledger(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, {"bot_name": ""})

    assert engine.ledger is None
    assert not (engine.session_dir / "owned_bots.json").exists()


def test_engine_derives_the_bot_name_under_bot_mode(tmp_path, monkeypatch):
    from condor.agents.config import load_full_config

    engine = _engine(tmp_path, monkeypatch, {"bot_mode": "bot", "bot_name": ""})

    assert engine.config["bot_name"] == NS
    # The session config — what per-session PnL attribution reads back — records
    # the resolved name, not the empty one it was started with.
    assert load_full_config(engine.session_dir, None)["bot_name"] == NS


def test_engine_carries_a_legacy_name_as_declared(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, {"bot_name": "river-scalper"})

    assert engine.ledger.declared == ["river-scalper"]
    assert engine.ledger.owns("river-scalper")


def test_experiment_engine_ledger_writes_no_file(tmp_path, monkeypatch):
    engine = _engine(
        tmp_path, monkeypatch, {"bot_name": NS, "execution_mode": "dry_run"}
    )

    assert engine.session_dir is None
    assert engine.ledger is not None and engine.ledger.path is None
    engine.ledger.note_deploy(f"{NS}-btc", now=1000.0)
    assert not list(engine.strategy.dir.rglob("owned_bots.json"))


def test_engine_adopts_a_live_bot_in_its_namespace(tmp_path, monkeypatch):
    from condor.fetchers import bot_performance

    engine = _engine(tmp_path, monkeypatch, {"bot_name": NS})

    async def _fake_perf(client):
        return {
            f"{NS}-btc-20260731-101500": {"bot_name": f"{NS}-btc-20260731-101500"},
            "someone-elses-bot-20260731-101500": {"bot_name": "someone-elses-bot"},
        }

    monkeypatch.setattr(bot_performance, "fetch_all_bot_performance", _fake_perf)
    asyncio.run(engine._adopt_running_bots(object()))

    assert engine.ledger.bases() == [f"{NS}-btc"]
    assert engine.ledger.owned()[0].origin == "adopted"
    assert engine._adoption_done


def test_engine_adoption_failure_is_non_fatal_and_retried(tmp_path, monkeypatch):
    from condor.fetchers import bot_performance

    engine = _engine(tmp_path, monkeypatch, {"bot_name": NS})

    async def _boom(client):
        raise RuntimeError("api down")

    monkeypatch.setattr(bot_performance, "fetch_all_bot_performance", _boom)
    asyncio.run(engine._adopt_running_bots(object()))

    assert engine.ledger.bases() == []
    assert not engine._adoption_done  # next tick tries again


def test_tick_blocks_a_foreign_bot_and_journals_it(tmp_path, monkeypatch):
    """The two halves of a tick joined by one ledger: the guard, then the journal."""
    engine = _engine(tmp_path, monkeypatch, {"bot_name": NS})
    callback = auto_approve_with_risk_check(
        engine.risk, RiskState(), ledger=engine.ledger
    )

    assert _drive(callback, _bot_call("deploy", "ema_trend_loop")) == "cancelled"

    engine._journal_ownership_violations(tick_num=1)
    journal = engine.journal.read_full()
    assert "bot_ownership_blocked" in journal
    assert "ema_trend_loop" in journal
    # Drained: the same refusal is not journaled again next tick.
    engine._journal_ownership_violations(tick_num=2)
    assert engine.journal.read_full().count("ema_trend_loop") == 1
