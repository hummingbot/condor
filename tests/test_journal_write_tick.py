"""An entry that is dated by its tick may not be written without one (CORR: tick #0).

``trading_agent_journal_write`` defaulted ``tick`` to ``0``, and nothing downstream
questioned it. A retry that dropped the argument therefore succeeded, answering
``{"written": true}``, and the decision landed in the journal as ``**#0**`` — which
the Decisions panel renders as **ERR**. A perfectly good deployment decision looked
like a failed one, and the visible tick sequence had a hole in it where the entry
should have been.

The same default quietly disabled the canvas's per-tick revision cap, which is
written ``if tick and written_this_tick >= MAX_REVISIONS_PER_TICK`` — at tick 0 an
agent could revise every section, every tick, forever.

Refused at the tool boundary, where the caller can still supply the tick. The
entry types that are *not* dated by a tick — a learning, a state snapshot — are
untouched.
"""

from types import SimpleNamespace

import pytest

from condor.agents import canvas
from mcp_servers.condor.tools import trading_agent

AGENT_ID = "brigado.grid_1"


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """A live (non-experiment) engine whose journal lands in tmp_path."""
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    eng = SimpleNamespace(
        is_experiment=False,
        session_dir=session_dir,
        strategy=SimpleNamespace(home=tmp_path),
    )
    from condor.agents import engine as engine_module

    monkeypatch.setattr(engine_module, "get_engine", lambda agent_id: eng)
    return eng


def _write(**kwargs):
    kwargs.setdefault("agent_id", AGENT_ID)
    kwargs.setdefault("text", "Tick 8 no change")
    return trading_agent.journal_write(**kwargs)


# ── The entry types dated by a tick ──


def test_an_action_with_no_tick_is_refused_rather_than_filed_under_zero(engine):
    result = _write(entry_type="action")

    assert "error" in result and "tick" in result["error"]
    assert result.get("written") is not True
    assert not (engine.session_dir / "journal.md").exists()


def test_a_canvas_revision_with_no_tick_is_refused_too(engine):
    """At tick 0 the per-tick revision cap does not apply at all."""
    result = _write(entry_type="canvas", section="thesis", text="Quoting wide.")

    assert "error" in result and "tick" in result["error"]
    assert canvas.read_sections(engine.session_dir) == {}


def test_a_negative_tick_is_refused_as_well(engine):
    assert "error" in _write(entry_type="action", tick=-1)


def test_the_refusal_names_the_entry_type_and_what_was_given(engine):
    """The agent has to be able to fix it without guessing."""
    message = _write(entry_type="action")["error"]

    assert "action" in message and ">= 1" in message


# ── A real tick still writes, and the undated types are untouched ──


def test_an_action_with_a_real_tick_is_written_under_that_tick(engine):
    assert _write(entry_type="action", tick=8) == {"written": True}

    decisions = (engine.session_dir / "journal.md").read_text()
    assert "**#8**" in decisions
    assert "**#0**" not in decisions


def test_a_learning_needs_no_tick(engine):
    assert _write(entry_type="learning", text="Spreads widen into the close") == {
        "written": True
    }


def test_a_state_snapshot_needs_no_tick(engine):
    assert _write(entry_type="state", text="SOL 142.10, 1 grid open") == {
        "written": True
    }


# ── Both snapshot writers share one renderer (ARCH-655) ──

_TOOL_CALLS = [
    {"name": "create_executor", "status": "completed", "input": {"amount": 10}},
    {"title": "get_prices", "input": "SOL-USDC", "output": "x" * 3000},
]


def _body(text: str) -> str:
    return text[text.index("## Executor State") :]


def _risk_section(text: str) -> str:
    return text[text.index("## Risk State") : text.index("## Agent Response")]


def _full_snapshot(tmp_path, risk_state: dict) -> str:
    from condor.agents.journal import JournalManager

    journal = JournalManager("agt.grid", session_dir=tmp_path / "full")
    return journal.save_full_snapshot(
        tick=7,
        timestamp="2026-09-17 10:00",
        system_prompt="SP",
        response_text="done",
        tool_calls=_TOOL_CALLS,
        executors_data="1 executor",
        risk_state=risk_state,
        duration=3.25,
    ).read_text()


def _experiment_snapshot(tmp_path, risk_state: dict) -> str:
    from condor.agents.journal import save_experiment_snapshot

    return save_experiment_snapshot(
        tmp_path / "exp",
        experiment_num=7,
        execution_mode="dry_run",
        timestamp="2026-09-17 10:00",
        system_prompt="SP",
        response_text="done",
        tool_calls=_TOOL_CALLS,
        executors_data="1 executor",
        risk_state=risk_state,
        duration=3.25,
        agent_key="claude-code",
    ).read_text()


def test_both_snapshot_writers_render_the_same_body(tmp_path):
    risk_state = {"max_drawdown_pct": 10, "drawdown_pct": 2.5, "max_leverage": 3}
    full = _full_snapshot(tmp_path, risk_state)
    experiment = _experiment_snapshot(tmp_path, risk_state)

    assert _body(full) == _body(experiment)
    assert full.startswith("# Snapshot #7 — 2026-09-17 10:00\n\n<details>")
    assert experiment.startswith(
        "# Experiment #7 — 2026-09-17 10:00\nMode: dry_run\nModel: claude-code\n\n"
    )
    # The output cap still applies, once.
    assert "x" * 2000 + "\n```" in full and "x" * 2001 not in full


def test_snapshot_risk_block_carries_the_leverage_line_the_prompt_shows(tmp_path):
    from condor.agents.journal import render_risk_lines

    risk_state = {"max_leverage": 5}
    prompt_lines = render_risk_lines(risk_state, bullet="")
    leverage = next(line for line in prompt_lines if line.startswith("Max Leverage"))
    assert leverage.startswith("Max Leverage: 5x")

    assert f"- {leverage}\n" in _risk_section(_full_snapshot(tmp_path, risk_state))
    assert f"- {leverage}\n" in _risk_section(
        _experiment_snapshot(tmp_path, risk_state)
    )


def test_an_empty_risk_state_still_renders_default_limits(tmp_path):
    # engine records a failed dry run with risk_state={}
    for text in (_full_snapshot(tmp_path, {}), _experiment_snapshot(tmp_path, {})):
        section = _risk_section(text)
        assert "- Position Size: $0.00 / $500.00 limit" in section
        assert "- Open Executors: 0 / 5 limit" in section
        assert "- Drawdown: disabled" in section
        assert "- Status: ACTIVE" in section
        assert "Max Leverage" not in section


def test_the_tick_prompt_risk_block_comes_from_the_shared_renderer():
    import inspect

    from condor.agents import journal, prompts

    assert prompts.render_risk_lines is journal.render_risk_lines
    assert "Position Size:" not in inspect.getsource(prompts)
