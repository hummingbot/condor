"""Stop and exit semantics: what a stop stops, and what manages a slot after it.

Three findings, one theme — an operator's idea of "stopped" and the system's did
not match, and nothing said so.

**SH-4**: Telegram's ``/stop`` cancels the answer being streamed and nothing
else. Operators read it as stopping the loop; it never touched a ``TickEngine``,
and Telegram has no control that does. Repurposing the command would be worse
than the gap — someone cutting a long answer short would be stopping live
trading — so every reply now names what is still running and where to stop it.

**DT-5**: ``ema_trend_loop`` ran in *executor* mode while deploying bots, so no
``[CONTROLLER MODE]`` block reached the prompt, the ledger enforced nothing, and
the playbook invented the name ``ema_trend_loop``. Hummingbot appends a deploy
timestamp, so ``manage_bots(status)`` never returned a bot by that name, the
loop concluded "none running" on every tick, and two timestamped instances of
the same book ran concurrently.

**LP-7**: every slot was opened with no ``upper_limit_price`` / ``lower_limit_price``,
which the tool's own docs say leaves a position sitting "out of range
indefinitely on the unprotected side". Stopping the loop therefore left real
on-chain liquidity with no exit at all, waiting for a tick that was never coming.
"""

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from condor.agents.ownership import bot_namespace, resolve_bot_name
from condor.runtime.loops import LoopSupervisor


def _front(path: str) -> dict:
    """A loop's ``default_config``, as shipped."""
    return yaml.safe_load(Path(path).read_text().split("---")[1])["default_config"]


DT_LOOP = "agents/directional_trader/loops/ema_trend_loop/loop.md"
LP_LOOP = "agents/solana_dex_lp_expert/loops/lp_slot_operator/loop.md"


# ── SH-4: /stop says what it did not stop ────────────────────────────────────


def _engine(agent_id: str, user_id: int, chat_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=agent_id,
        user_id=user_id,
        chat_id=chat_id,
        agent=SimpleNamespace(slug=agent_id.split(".")[0]),
        strategy=SimpleNamespace(slug="loop"),
    )


def _supervisor(*engines) -> LoopSupervisor:
    supervisor = LoopSupervisor()
    for engine in engines:
        supervisor._engines[engine.agent_id] = engine
    return supervisor


def test_a_persons_running_loops_are_findable():
    """The answer ``/stop`` needs: what is this person still running?"""
    mine = _engine("mm.pmm_op_1", user_id=7, chat_id=7)
    theirs = _engine("lp.slots_1", user_id=9, chat_id=9)

    found = _supervisor(mine, theirs).for_owner(7, 7)

    assert [e.agent_id for e in found] == ["mm.pmm_op_1"]


def test_a_dashboard_launched_loop_still_belongs_to_its_owner():
    """``chat_id`` 0 is a web launch, not a loop without an owner."""
    web = _engine("mm.pmm_op_2", user_id=7, chat_id=0)

    assert [e.agent_id for e in _supervisor(web).for_owner(7, 7)] == ["mm.pmm_op_2"]


def test_another_persons_loop_is_never_listed():
    """Keyed on the authenticated owner, not on the chat: ``chat_defaults`` is a
    global map, so selecting on the chat alone is the substitution SEC-164
    removed from every other resolution path."""
    theirs = _engine("mm.pmm_op_1", user_id=9, chat_id=7)

    assert _supervisor(theirs).for_owner(7, 7) == []


def test_nothing_running_is_an_empty_list():
    assert _supervisor().for_owner(7, 7) == []


def test_the_note_names_the_loops_and_where_to_stop_them(monkeypatch):
    from condor.runtime import loops as loops_mod
    from handlers.agents import _running_loops_note

    monkeypatch.setattr(
        loops_mod, "get_supervisor", lambda: _supervisor(_engine("mm.pmm_op_1", 7, 7))
    )

    note = _running_loops_note(7, 7)

    assert "did NOT stop" in note
    assert "mm.pmm_op_1" in note
    assert "dashboard" in note
    assert 'control_agent(action="stop"' in note


def test_the_note_is_empty_when_nothing_is_running(monkeypatch):
    """No standing noise on a command most people use with no loop at all."""
    from condor.runtime import loops as loops_mod
    from handlers.agents import _running_loops_note

    monkeypatch.setattr(loops_mod, "get_supervisor", lambda: _supervisor())

    assert _running_loops_note(7, 7) == ""


def test_a_registry_that_cannot_be_read_costs_the_note_and_nothing_else(monkeypatch):
    """A chat reply must not fail because the registry raised."""
    from condor.runtime import loops as loops_mod
    from handlers.agents import _running_loops_note

    def boom():
        raise RuntimeError("no registry")

    monkeypatch.setattr(loops_mod, "get_supervisor", boom)

    assert _running_loops_note(7, 7) == ""


def test_stop_still_only_cancels_the_reply():
    """The deliberate half: this command must NOT stop a loop.

    Someone cutting a long answer short would otherwise be stopping live
    trading. Pinned because "make /stop stop the loop" is the obvious reading of
    the report, and it is the dangerous one.
    """
    from handlers.agents import stop_command

    source = inspect.getsource(stop_command)

    assert "runtime.abort" in source
    assert "_running_loops_note" in source
    for stopper in ("get_supervisor().stop", ".stop(", "shutdown("):
        assert stopper not in source.replace("_running_loops_note", "")


# ── DT-5: the loop reads the bot it owns ─────────────────────────────────────


def test_the_dt_loop_runs_in_controller_mode():
    """Without this it deploys bots while the ledger enforces nothing."""
    config = _front(DT_LOOP)

    assert config["bot_mode"] == "bot"


def test_its_bot_name_is_derived_as_its_own_namespace():
    """``bot_mode: bot`` with no name derives the namespace, so the name the
    loop deploys under is the one the risk gate already allows."""
    config = _front(DT_LOOP)

    resolved = resolve_bot_name(config, "directional_trader", "ema_trend_loop")

    assert resolved == bot_namespace("directional_trader", "ema_trend_loop")
    assert resolved  # non-empty == controller mode on


def test_the_playbook_no_longer_hardcodes_a_bot_name():
    """The literal that produced two concurrent instances of one book."""
    text = Path(DT_LOOP).read_text()
    body = text.split("---", 2)[2]  # past the frontmatter's explanatory comment

    assert 'bot_name="ema_trend_loop"' not in body
    assert "Look for bot named" not in body


def test_the_playbook_points_at_the_block_that_knows():
    """``[CONTROLLER MODE]`` lists "Bots you own right now"; a status call
    returns the whole fleet under names the loop never typed."""
    body = Path(DT_LOOP).read_text().split("---", 2)[2]

    assert "[CONTROLLER MODE]" in body
    assert "Bots you own right now" in body


# ── LP-7: a slot nobody is managing still closes itself ──────────────────────


def test_every_slot_is_opened_with_both_autonomous_limits():
    """The tool's own doc: set BOTH or the position sits out of range
    indefinitely on the unprotected side."""
    body = Path(LP_LOOP).read_text()
    open_call = next(
        line for line in body.splitlines() if "create_lp_executor(" in line
    )

    assert "lower_limit_price=" in open_call
    assert "upper_limit_price=" in open_call


def test_the_margin_is_configurable_and_outside_the_range():
    config = _front(LP_LOOP)

    assert config["autonomous_exit_margin_pct"] > 0


def test_the_margin_leaves_the_loops_own_grace_window_in_charge():
    """These are the backstop, not the strategy: they fire only while
    OUT_OF_RANGE, and sit outside the range so a tick-driven exit decides first."""
    body = Path(LP_LOOP).read_text()

    assert "autonomous_exit_margin_pct/100" in body
    assert "mandatory" in body.lower()
    assert "out_of_range_max_sec" in body


@pytest.mark.parametrize("key", ["take_profit_pct", "stop_loss_pct"])
def test_the_tick_driven_exits_are_untouched(key):
    """The autonomous limits are added to the loop's own exits, not instead."""
    assert _front(LP_LOOP)[key] > 0
