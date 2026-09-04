"""A run is a run, whatever door it came through (FEAT-111).

``list_all_runs`` unions four enumerations that used to be two, so the three
properties asserted here are the ones the feature rests on: an agent with no
strategy at all still has runs (its conversations), an agent that both loops and
converses gets one time-ordered list rather than two places to look, and the
whole union is assembled from metadata — a ``status.json``, a ``meta.json`` —
with no Hummingbot call and no transcript read anywhere. The fourth is the
window: a chatty install pages rather than listing its archive.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from condor import paths
from condor.agents import strategy as strategy_module
from condor.agents.all_runs import (
    KIND_CONVERSATION,
    KIND_DELEGATION,
    KIND_EXPERIMENT,
    KIND_SESSION,
    list_all_runs,
    run_id_for,
)

USER = 7


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    """The strategy store's root; the durable roots come from ``conftest``."""
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path / "agents")


def _write_strategy(root: Path, agent_slug: str, sslug: str, name: str) -> Path:
    d = root / "agents" / agent_slug / "strategies" / sslug
    d.mkdir(parents=True, exist_ok=True)
    (d / "strategy.md").write_text(f"---\nname: {name}\n---\n\nPlaybook.\n")
    return d


def _write_session(strategy_dir: Path, num: int, *, ticks: int = 1) -> Path:
    d = strategy_dir / "sessions" / f"session_{num}"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["# Journal", "", "## Ticks", ""]
    for t in range(1, ticks + 1):
        lines.append(f"- tick#{t} | 2026-09-01 10:{t:02d} | actions=0 | did a thing")
    (d / "journal.md").write_text("\n".join(lines) + "\n")
    return d


def _write_experiment(strategy_dir: Path, num: int) -> Path:
    d = strategy_dir / "dry_runs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"experiment_{num}.md"
    path.write_text(
        f"# Experiment #{num} — 2026-09-01 10:00:00\n"
        "Mode: dry_run\nModel: claude-code\n\n"
        "## Agent Response\nLooks flat, holding.\n"
    )
    return path


def _write_conversation(
    user_id: int,
    conv_id: str,
    *,
    agent_slug: str = "",
    title: str = "",
    created: datetime | None = None,
    updated: datetime | None = None,
) -> Path:
    d = paths.conversation_dir(user_id, conv_id)
    d.mkdir(parents=True, exist_ok=True)
    born = created or datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    (d / "meta.json").write_text(
        json.dumps(
            {
                "id": conv_id,
                "user_id": user_id,
                "agent_slug": agent_slug,
                "title": title,
                "turn_count": 4,
                "created_at": born.isoformat(),
                "updated_at": (updated or born).isoformat(),
            }
        )
    )
    (d / "transcript.jsonl").write_text(
        json.dumps({"role": "user", "text": "hello"}) + "\n"
    )
    return d


def _write_delegation(
    user_id: int,
    task_id: str,
    *,
    agent_slug: str,
    task: str = "go and look",
    state: str = "done",
    started: float = 1_788_000_000.0,
) -> Path:
    d = paths.delegation_dir(user_id, task_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(
        json.dumps(
            {
                "state": state,
                "agent_slug": agent_slug,
                "user_id": user_id,
                "task": task,
                "kind": "delegate",
                "started_at": started,
                "ended_at": started + 60,
                "updated_at": started + 60,
            }
        )
    )
    return d


# ── The bug this feature exists for ──


def test_an_agent_with_no_strategies_still_has_its_conversations(tmp_path):
    """Condor's own case: no loop, no experiment, and a rail that was empty."""
    _write_conversation(USER, "c-one", title="What is the fleet doing?")

    rows = list_all_runs("condor", USER)

    assert [r["kind"] for r in rows] == [KIND_CONVERSATION]
    assert rows[0]["run_id"] == "c:c-one"
    assert rows[0]["id"] == "c-one"
    assert rows[0]["title"] == "What is the fleet doing?"
    # A chat belongs to no strategy, and says so rather than inventing one.
    assert rows[0]["strategy_slug"] == ""
    assert rows[0]["tick_count"] == 0


def test_an_unbound_conversation_is_a_conversation_with_condor(tmp_path):
    """The empty slug on disk *means* Condor, and must not read as "no agent"."""
    _write_conversation(USER, "c-unbound", agent_slug="")
    _write_conversation(USER, "c-brigado", agent_slug="brigado")

    assert [r["id"] for r in list_all_runs("condor", USER)] == ["c-unbound"]
    assert [r["id"] for r in list_all_runs("brigado", USER)] == ["c-brigado"]


# ── One rail, four kinds ──


def test_an_agent_that_loops_and_converses_shows_both_interleaved(tmp_path):
    sdir = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    session = _write_session(sdir, 1, ticks=3)
    _write_experiment(sdir, 1)

    started = session.joinpath("journal.md").stat().st_mtime
    older = datetime.fromtimestamp(started - 3600, timezone.utc)
    newer = datetime.fromtimestamp(started + 3600, timezone.utc)
    _write_conversation(USER, "c-old", agent_slug="brigado", created=older)
    _write_conversation(USER, "c-new", agent_slug="brigado", created=newer)
    _write_delegation(
        USER, "d-1", agent_slug="brigado", started=started + 7200, state="error"
    )

    rows = list_all_runs("brigado", USER)
    kinds = [r["kind"] for r in rows]

    assert set(kinds) == {
        KIND_SESSION,
        KIND_EXPERIMENT,
        KIND_DELEGATION,
        KIND_CONVERSATION,
    }
    # Newest first, on one axis, with the two new kinds sorted among the loops
    # rather than appended after them.
    stamps = [r["started_at"] or 0.0 for r in rows]
    assert stamps == sorted(stamps, reverse=True)
    assert rows[0]["run_id"] == "d:d-1"
    assert rows[1]["run_id"] == "c:c-new"
    assert rows[-1]["run_id"] == "c:c-old"

    delegation = rows[0]
    assert delegation["error"] is True
    assert delegation["title"] == "go and look"
    assert delegation["strategy_slug"] == ""


def test_a_loop_run_keeps_everything_it_said_and_gains_the_new_id(tmp_path):
    sdir = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    _write_session(sdir, 3, ticks=5)

    (row,) = [r for r in list_all_runs("brigado", USER) if r["kind"] == KIND_SESSION]

    assert row["run_id"] == "s:3"
    assert row["id"] == "3"
    assert row["number"] == 3
    assert row["tick_count"] == 5
    assert row["agent_id"] == "brigado.brl_mm_3"
    assert row["strategy_slug"] == "brl_mm"
    assert row["strategy_name"] == "BRL MM"


def test_the_id_grammar_is_one_letter_and_an_opaque_id():
    assert run_id_for(KIND_SESSION, 3) == "s:3"
    assert run_id_for(KIND_EXPERIMENT, 1) == "e:1"
    assert run_id_for(KIND_DELEGATION, "abc123") == "d:abc123"
    assert run_id_for(KIND_CONVERSATION, "7f3a") == "c:7f3a"


# ── Scoping, cost and the window ──


def test_conversations_are_scoped_to_the_person_asking(tmp_path):
    """A conversation is private; two people see different rails, correctly."""
    _write_conversation(USER, "mine", agent_slug="brigado")
    _write_conversation(99, "theirs", agent_slug="brigado")

    assert [r["id"] for r in list_all_runs("brigado", USER)] == ["mine"]
    assert [r["id"] for r in list_all_runs("brigado", 99)] == ["theirs"]
    # Admin scope (``None``) spans every owner.
    assert {r["id"] for r in list_all_runs("brigado", None)} == {"mine", "theirs"}


def test_listing_reads_metadata_and_never_a_transcript(tmp_path, monkeypatch):
    """The whole cost bargain: a meta and a status file, nothing bigger.

    Guarded by opening every file the listing touches. A transcript read here
    would be paid on every 5s poll of the rail, for a row nobody has opened.
    """
    _write_conversation(USER, "c-1", agent_slug="brigado")
    _write_delegation(USER, "d-1", agent_slug="brigado")
    sdir = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    _write_session(sdir, 1, ticks=2)

    opened: list[str] = []
    real_open = Path.open

    def watching_open(self, *args, **kwargs):
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", watching_open)
    real_read_text = Path.read_text

    def watching_read_text(self, *args, **kwargs):
        opened.append(self.name)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", watching_read_text)

    rows = list_all_runs("brigado", USER)

    assert len(rows) == 3
    assert "transcript.jsonl" not in opened
    assert "transcript_archive.jsonl" not in opened


def test_no_hummingbot_client_is_ever_built(tmp_path, monkeypatch):
    """Disk only — the property that licenses the rail's five-second poll."""
    import config_manager

    def explode(*_a, **_k):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("listing runs must not reach the Hummingbot API")

    monkeypatch.setattr(config_manager, "get_client", explode)
    _write_conversation(USER, "c-1", agent_slug="brigado")
    _write_delegation(USER, "d-1", agent_slug="brigado")

    assert len(list_all_runs("brigado", USER)) == 2


def test_hundreds_of_conversations_page_rather_than_all_arriving(tmp_path):
    born = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    for i in range(150):
        _write_conversation(
            USER,
            f"c-{i:03d}",
            agent_slug="brigado",
            created=born + timedelta(minutes=i),
        )

    page = list_all_runs("brigado", USER, limit=25)

    assert len(page) == 25
    # Newest first, so the window is the *top* of the archive, not a slice of
    # whatever the directory listing happened to yield.
    assert page[0]["id"] == "c-149"
    assert page[-1]["id"] == "c-125"
    assert len(list_all_runs("brigado", USER, limit=200)) == 150


# ── The route ──


def _call_route(slug="brigado", **kw):
    import asyncio
    from types import SimpleNamespace

    from condor.web.routes.agents import list_agent_runs

    user = SimpleNamespace(id=USER, is_admin=False)
    return asyncio.run(list_agent_runs(slug, user=user, **kw))


def _write_agent(root: Path, slug: str, name: str) -> Path:
    d = root / "agents" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(f"---\nname: {name}\n---\n\nBody.\n")
    return d


def test_the_route_serves_the_union_in_the_new_grammar(monkeypatch, tmp_path):
    from condor.agents import agent as agent_module

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    _write_agent(tmp_path, "brigado", "Brigado")
    sdir = _write_strategy(tmp_path, "brigado", "brl_mm", "BRL MM")
    _write_session(sdir, 1, ticks=2)
    _write_conversation(USER, "c-1", agent_slug="brigado", title="Hello")

    rows = _call_route().runs
    by_kind = {r.kind: r for r in rows}

    assert set(by_kind) == {KIND_SESSION, KIND_CONVERSATION}
    assert by_kind[KIND_SESSION].run_id == "s:1"
    assert by_kind[KIND_CONVERSATION].run_id == "c:c-1"
    assert by_kind[KIND_CONVERSATION].title == "Hello"


def test_the_route_bounds_what_a_caller_can_ask_for(monkeypatch, tmp_path):
    """The cap is what stops a hand-typed ``?limit=`` undoing the 5s poll."""
    from condor.agents import agent as agent_module
    from condor.web.routes.agents import MAX_RUN_LIMIT

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    _write_agent(tmp_path, "brigado", "Brigado")

    seen: list[int] = []
    import condor.agents.all_runs as all_runs_module

    real = all_runs_module.list_all_runs

    def watching(slug, user_id, *, limit):
        seen.append(limit)
        return real(slug, user_id, limit=limit)

    monkeypatch.setattr(all_runs_module, "list_all_runs", watching)

    _call_route(limit=10)
    _call_route(limit=10_000)
    _call_route(limit=0)
    assert seen == [10, MAX_RUN_LIMIT, 1]


def test_a_conversation_of_another_person_is_not_in_this_rail(monkeypatch, tmp_path):
    from condor.agents import agent as agent_module

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path / "agents")
    _write_agent(tmp_path, "brigado", "Brigado")
    _write_conversation(USER + 1, "not-mine", agent_slug="brigado")

    assert _call_route().runs == []
