"""The session canvas: a bounded, closed-set narrative the agent revises (FEAT-036).

The canvas is inlined into *every* tick prompt and prompt caching cannot help
(the volatile risk/core-data blocks sit ahead of it), so the size caps here are
the feature's only real cost control — they are asserted, not assumed.
"""

from types import SimpleNamespace

import pytest

from condor.agents import canvas


@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / "session_1"
    d.mkdir()
    return d


# ── Section validation ──


def test_unknown_section_is_rejected_without_writing(session_dir):
    result = canvas.write_section(session_dir, "vibes", "anything", tick=1)
    assert "error" in result
    assert "vibes" in result["error"]
    assert not (session_dir / canvas.CANVAS_FILE).exists()


def test_section_name_is_case_and_space_insensitive(session_dir):
    assert canvas.write_section(session_dir, "  Thesis ", "wide spreads", tick=1) == {
        "written": True,
        "section": "thesis",
    }
    assert canvas.read_sections(session_dir)["thesis"] == "wide spreads"


def test_empty_text_is_rejected(session_dir):
    assert "error" in canvas.write_section(session_dir, "thesis", "   ", tick=1)


def test_no_session_dir_skips_benignly():
    assert "skipped" in canvas.write_section(None, "thesis", "x", tick=1)


# ── Truncation at the cap ──


def test_long_section_is_truncated_to_the_cap(session_dir):
    result = canvas.write_section(
        session_dir, "thesis", "x" * (canvas.MAX_SECTION_CHARS + 500), tick=1
    )
    assert result["truncated_to"] == canvas.MAX_SECTION_CHARS
    assert len(canvas.read_sections(session_dir)["thesis"]) == canvas.MAX_SECTION_CHARS


def test_whole_canvas_stays_bounded_by_the_caps(session_dir):
    """Four maximal sections is the worst case the prompt ever has to carry."""
    for section in canvas.CANVAS_SECTIONS:
        canvas.write_section(session_dir, section, "y" * 5000, tick=1)
    body = canvas.read_canvas(session_dir)
    assert len(body) < canvas.MAX_SECTION_CHARS * len(canvas.CANVAS_SECTIONS) + 200


# ── Section replacement preserves siblings ──


def test_writing_one_section_leaves_the_others_intact(session_dir):
    canvas.write_section(session_dir, "thesis", "range-bound", tick=1)
    canvas.write_section(session_dir, "working", "tight spreads fill", tick=2)
    canvas.write_section(session_dir, "thesis", "trending now", tick=3)

    sections = canvas.read_sections(session_dir)
    assert sections["thesis"] == "trending now"
    assert sections["working"] == "tight spreads fill"


def test_canvas_uses_the_closed_header_set(session_dir):
    canvas.write_section(session_dir, "thesis", "t", tick=1)
    body = canvas.read_canvas(session_dir)
    for title in canvas.SECTION_TITLES.values():
        assert f"## {title}" in body


def test_multiline_section_body_round_trips(session_dir):
    canvas.write_section(session_dir, "questions", "- one\n- two", tick=1)
    canvas.write_section(session_dir, "thesis", "t", tick=1)
    assert canvas.read_sections(session_dir)["questions"] == "- one\n- two"


def test_absent_canvas_reads_empty(session_dir):
    assert canvas.read_canvas(session_dir) == ""
    assert canvas.read_sections(session_dir) == {}
    assert canvas.last_revised_tick(session_dir) == 0


# ── Revision log ──


def test_every_revision_is_logged_in_full(session_dir):
    canvas.write_section(session_dir, "thesis", "first", tick=1)
    canvas.write_section(session_dir, "thesis", "second", tick=4)

    revisions = canvas.recent_revisions(session_dir)
    assert [r["text"] for r in revisions] == ["second", "first"]  # newest first
    assert [r["tick"] for r in revisions] == [4, 1]
    assert canvas.last_revised_tick(session_dir) == 4


def test_revision_text_is_flattened_to_one_line(session_dir):
    canvas.write_section(session_dir, "questions", "- one\n- two", tick=1)
    assert canvas.recent_revisions(session_dir)[0]["text"] == "- one - two"


def test_revision_log_is_trimmed_fifo(session_dir):
    for tick in range(1, canvas.MAX_REVISION_ENTRIES + 10):
        canvas.write_section(session_dir, "thesis", f"t{tick}", tick=tick)

    revisions = canvas.recent_revisions(session_dir, limit=1000)
    assert len(revisions) == canvas.MAX_REVISION_ENTRIES
    assert revisions[0]["text"] == f"t{canvas.MAX_REVISION_ENTRIES + 9}"


def test_thrash_guard_caps_revisions_per_tick(session_dir):
    for section in canvas.CANVAS_SECTIONS:
        assert canvas.write_section(session_dir, section, "x", tick=7)["written"]

    blocked = canvas.write_section(session_dir, "thesis", "again", tick=7)
    assert "skipped" in blocked
    assert canvas.read_sections(session_dir)["thesis"] == "x"

    # The next tick is free again.
    assert canvas.write_section(session_dir, "thesis", "again", tick=8)["written"]


# ── Staleness nudge ──


def _tracker(**kw):
    return canvas.NudgeTracker(nudge_ticks=12, band_usd=25.0, **kw)


def test_quiet_tick_produces_no_nudge():
    t = _tracker()
    t.next(tick=1, last_revised_tick=1, open_count=0, total_pnl=0.0)
    assert t.next(tick=2, last_revised_tick=1, open_count=0, total_pnl=0.0) == ""


def test_position_flip_nudges():
    t = _tracker()
    t.next(tick=1, last_revised_tick=1, open_count=0, total_pnl=0.0)
    nudge = t.next(tick=2, last_revised_tick=1, open_count=2, total_pnl=0.0)
    assert "you now hold positions" in nudge


def test_position_flip_back_to_flat_nudges():
    t = _tracker()
    t.next(tick=1, last_revised_tick=1, open_count=3, total_pnl=0.0)
    nudge = t.next(tick=2, last_revised_tick=1, open_count=0, total_pnl=0.0)
    assert "positions are all closed" in nudge


def test_pnl_band_cross_nudges():
    t = _tracker()
    t.next(tick=1, last_revised_tick=1, open_count=1, total_pnl=0.0)
    assert t.next(tick=2, last_revised_tick=1, open_count=1, total_pnl=20.0) == ""
    nudge = t.next(tick=3, last_revised_tick=1, open_count=1, total_pnl=40.0)
    assert "PnL has moved" in nudge


def test_a_revision_re_anchors_the_pnl_band():
    t = _tracker()
    t.next(tick=1, last_revised_tick=1, open_count=1, total_pnl=0.0)
    # The agent revises at tick 2 with PnL already at $40 — the band restarts there.
    assert t.next(tick=2, last_revised_tick=2, open_count=1, total_pnl=40.0) == ""
    assert t.next(tick=3, last_revised_tick=2, open_count=1, total_pnl=50.0) == ""


def test_error_on_the_previous_tick_nudges():
    t = _tracker()
    t.next(tick=1, last_revised_tick=1, open_count=1, total_pnl=0.0)
    nudge = t.next(
        tick=2, last_revised_tick=1, open_count=1, total_pnl=0.0, had_error=True
    )
    assert "errored" in nudge


def test_staleness_nudges_after_nudge_ticks():
    t = _tracker()
    # Nothing happens for eleven quiet ticks after the revision at #1...
    for tick in range(1, 13):
        assert t.next(tick=tick, last_revised_tick=1, open_count=1, total_pnl=0.0) == ""
    # ...and on the twelfth the canvas is called out as stale.
    nudge = t.next(tick=13, last_revised_tick=1, open_count=1, total_pnl=0.0)
    assert "12 ticks ago" in nudge
    assert "tick #1" in nudge


def test_rate_limit_suppresses_a_persistent_condition():
    t = _tracker()
    t.next(tick=1, last_revised_tick=1, open_count=0, total_pnl=0.0)
    assert t.next(tick=2, last_revised_tick=1, open_count=1, total_pnl=0.0)  # flip
    # An error persists for the next few ticks — silence until the gap elapses.
    for tick in range(3, 8):
        assert (
            t.next(
                tick=tick,
                last_revised_tick=1,
                open_count=1,
                total_pnl=0.0,
                had_error=True,
            )
            == ""
        )
    assert t.next(
        tick=8, last_revised_tick=1, open_count=1, total_pnl=0.0, had_error=True
    )


def test_empty_canvas_nudge_asks_for_an_opening_thesis():
    t = _tracker()
    t.next(tick=1, last_revised_tick=0, open_count=0, total_pnl=0.0)
    nudge = t.next(tick=12, last_revised_tick=0, open_count=0, total_pnl=0.0)
    assert "still empty" in nudge
    assert "#0" not in nudge


# ── Tool surface: the canvas rides on journal_write ──


def _journal_write(monkeypatch, engine, **kwargs):
    """Call the MCP tool with ``get_engine`` resolving to ``engine``."""
    from condor.agents import engine as engine_module
    from mcp_servers.condor.tools import trading_agent

    monkeypatch.setattr(engine_module, "get_engine", lambda agent_id: engine)
    return trading_agent.journal_write(
        agent_id="brigado.grid_1", entry_type="canvas", **kwargs
    )


def _live_engine(session_dir, agent_dir):
    return SimpleNamespace(
        is_experiment=False,
        session_dir=session_dir,
        strategy=SimpleNamespace(home=agent_dir),
    )


def test_tool_writes_one_section_and_logs_a_revision(
    monkeypatch, session_dir, tmp_path
):
    result = _journal_write(
        monkeypatch,
        _live_engine(session_dir, tmp_path),
        text="Quoting wide into the open.",
        section="thesis",
        tick=3,
    )
    assert result == {"written": True, "section": "thesis"}
    assert canvas.read_sections(session_dir) == {
        "thesis": "Quoting wide into the open."
    }
    assert canvas.last_revised_tick(session_dir) == 3


def test_tool_truncates_past_the_cap(monkeypatch, session_dir, tmp_path):
    result = _journal_write(
        monkeypatch,
        _live_engine(session_dir, tmp_path),
        text="z" * (canvas.MAX_SECTION_CHARS + 1),
        section="working",
        tick=1,
    )
    assert result["truncated_to"] == canvas.MAX_SECTION_CHARS


def test_tool_leaves_siblings_intact(monkeypatch, session_dir, tmp_path):
    engine = _live_engine(session_dir, tmp_path)
    _journal_write(monkeypatch, engine, text="a", section="thesis", tick=1)
    _journal_write(monkeypatch, engine, text="b", section="questions", tick=2)
    assert canvas.read_sections(session_dir) == {"thesis": "a", "questions": "b"}


def test_experiment_mode_skips_and_writes_nothing(monkeypatch, session_dir, tmp_path):
    """Experiments keep no journal — and so must keep no canvas."""
    experiment = SimpleNamespace(
        is_experiment=True,
        session_dir=session_dir,
        strategy=SimpleNamespace(home=tmp_path),
    )
    result = _journal_write(
        monkeypatch, experiment, text="thesis", section="thesis", tick=1
    )
    assert "skipped" in result
    assert not (session_dir / canvas.CANVAS_FILE).exists()


def test_a_canvas_write_does_not_disturb_the_journal(
    monkeypatch, session_dir, tmp_path
):
    from condor.agents.journal import JournalManager

    jm = JournalManager("brigado.grid_1", session_dir=session_dir, agent_dir=tmp_path)
    jm.append_action(1, "opened a grid", "range-bound")

    _journal_write(
        monkeypatch,
        _live_engine(session_dir, tmp_path),
        text="still range-bound",
        section="thesis",
        tick=1,
    )
    assert "opened a grid" in jm.get_recent_decisions(count=5)


# ── Prompt: the canvas block ──


def _prompt(config, **kwargs):
    from condor.agents.prompts import build_tick_prompt

    agent = SimpleNamespace(instructions="", agent_key="claude-code", slug="brigado")
    strategy = SimpleNamespace(
        instructions="Do the thing.",
        agent_key="claude-code",
        slug="grid",
        agent_slug="brigado",
        home=None,
    )
    kwargs.setdefault("recent_decisions", "")
    return build_tick_prompt(
        agent=agent,
        strategy=strategy,
        config=config,
        core_data={},
        learnings="",
        summary="",
        risk_state={},
        tick_number=1,
        agent_id="brigado.grid_1",
        cached_routines_section="",
        **kwargs,
    )


def test_loop_prompt_carries_the_canvas_and_its_protocol():
    prompt = _prompt({"execution_mode": "loop"}, canvas="## Thesis\n\nWide.")
    assert "[SESSION CANVAS" in prompt
    assert "## Thesis\n\nWide." in prompt
    assert 'entry_type="canvas"' in prompt


def test_empty_canvas_asks_for_an_opening_thesis():
    prompt = _prompt({"execution_mode": "loop"})
    assert "(empty — write your opening thesis this tick)" in prompt


def test_nudge_rides_along_when_present():
    prompt = _prompt(
        {"execution_mode": "loop"}, canvas="## Thesis\n\nWide.", canvas_nudge="Stale!"
    )
    assert "Stale!" in prompt
    assert prompt.index("## Thesis") < prompt.index("Stale!")


@pytest.mark.parametrize("mode", ["dry_run", "run_once"])
def test_experiments_never_see_a_canvas(mode):
    prompt = _prompt({"execution_mode": mode}, canvas="## Thesis\n\nWide.")
    assert "[SESSION CANVAS" not in prompt
    assert 'entry_type="canvas"' not in prompt


def test_canvas_block_sits_after_the_volatile_data_blocks():
    """Prompt caching cannot help here, so position is about readability only —
    but the canvas must stay last so the nudge is the final thing the agent reads."""
    prompt = _prompt(
        {"execution_mode": "loop"},
        canvas="## Thesis\n\nWide.",
        recent_decisions="- **#1** did a thing",
    )
    assert prompt.index("[RECENT DECISIONS") < prompt.index("[SESSION CANVAS")
