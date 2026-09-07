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
