"""Unit tests for delegation history -- the disk-backed read side (FEAT-035).

The live registry dies with the process; these tests cover what survives it:
the record rebuilt from ``status.json``, the transcript rebuilt from
``events.json``, the two pre-FEAT-051 shapes still readable in the agent
directories, and the "the process died mid-task" reconciliation.

Every reader is scoped by owner (FEAT-051): ``user_id`` first, ``None`` only
from an admin path.
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from condor import paths
from condor.agents import agent as agent_module
from condor.agents import consult as consult_module
from condor.agents import delegate as delegate_module
from condor.agents import delegation_history as history_module
from condor.agents.delegate import start_delegation
from condor.agents.delegation_history import (
    DELEGATION_TRANSCRIPT_FILENAME,
    list_history,
    read_history,
    read_history_events,
)

LEGACY_MD = """# Delegation scout-delegate-legacy01

- **Status:** done
- **Agent:** scout
- **Server:** local
- **Tool calls:** 7

## Task

check the SOL pools

## Session

💭 **Reasoning**

> thinking about pools

🔧 **1. get_market_data** (completed)

## Result

three pools worth watching
"""


USER = 7


class _FakeBot:
    async def send_message(self, *a, **kw):
        pass


def _write_agent(root, slug):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(
        f"---\nname: {slug}\nwhen_to_consult: always\n---\n\nBody.\n"
    )
    return d


def _delegations_dir(root, slug):
    d = root / slug / "delegations"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(autouse=True)
def _clean_registry():
    delegate_module._delegations.clear()
    yield
    delegate_module._delegations.clear()


def _run_delegation(monkeypatch, root, slug="scout", task="scan SOL pools"):
    """Drive one delegation to completion, emitting a thought and a tool call."""
    from condor.acp.client import ThoughtChunk, ToolCallEvent, ToolCallUpdate

    async def fake_run(*, event_sink, **kw):
        event_sink(ThoughtChunk(text="weighing the pools"))
        event_sink(
            ToolCallEvent(
                tool_call_id="tc-1",
                title="get_market_data",
                status="in_progress",
                input={"pair": "SOL-USDC"},
            )
        )
        event_sink(ToolCallUpdate(tool_call_id="tc-1", status="completed", output="ok"))
        return "three pools worth watching"

    monkeypatch.setattr(consult_module, "_run_agent_to_completion", fake_run)

    async def scenario():
        dt = await start_delegation(
            agent_slug=slug,
            user_id=USER,
            chat_id=42,
            server_name="local",
            task=task,
            bot=_FakeBot(),
        )
        await dt._task
        return dt

    return asyncio.run(scenario())


def test_finished_delegation_is_readable_after_the_registry_is_gone(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")

    dt = _run_delegation(monkeypatch, tmp_path)
    delegate_module._delegations.clear()  # the restart

    record = read_history(USER, dt.task_id)
    assert record is not None
    assert record["status"] == "done"
    assert record["agent"] == "scout"
    assert record["task"] == "scan SOL pools"
    assert record["result"] == "three pools worth watching"
    assert record["server_name"] == "local"
    assert record["user_id"] == 7
    assert record["tool_count"] == 1
    assert record["started_at"] > 0
    assert record["ended_at"] >= record["started_at"]

    assert [r["task_id"] for r in list_history(user_id=USER)] == [dt.task_id]
    # And a stranger cannot even name it: the id is a path segment now.
    assert read_history(USER + 1, dt.task_id) is None
    assert list_history(user_id=USER + 1) == []


def test_transcript_survives_with_the_same_shape_the_wire_uses(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")

    dt = _run_delegation(monkeypatch, tmp_path)
    sidecar = paths.delegation_dir(USER, dt.task_id) / "events.json"
    assert json.loads(sidecar.read_text())["events"] == delegate_module.events_for_wire(
        dt.events
    )

    delegate_module._delegations.clear()
    events, markdown = read_history_events(USER, dt.task_id)
    assert markdown == ""  # structured events win; no fallback needed
    assert [e["type"] for e in events] == ["thought", "tool"]
    tool = events[1]
    assert tool["name"] == "get_market_data"
    assert tool["status"] == "completed"
    assert tool["output"] == "ok"


def test_legacy_transcript_without_a_status_file_is_still_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")
    (_delegations_dir(tmp_path, "scout") / "scout-delegate-legacy01.md").write_text(
        LEGACY_MD
    )

    record = read_history(None, "scout-delegate-legacy01")
    assert record is not None
    assert record["status"] == "done"
    assert record["agent"] == "scout"
    assert record["task"] == "check the SOL pools"
    assert record["result"] == "three pools worth watching"
    assert record["tool_count"] == 7
    # Nobody's, so nobody but an admin can see it -- the route relies on this.
    assert record["user_id"] == 0

    events, markdown = read_history_events(None, "scout-delegate-legacy01")
    assert events == []
    assert "🔧 **1. get_market_data**" in markdown


def test_a_task_the_process_died_on_reads_as_interrupted(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")
    (
        _delegations_dir(tmp_path, "scout") / "scout-delegate-ghost01.status.json"
    ).write_text(
        json.dumps(
            {
                "state": "running",
                "task_id": "scout-delegate-ghost01",
                "agent_slug": "scout",
                "user_id": 7,
                "task": "a task nobody finished",
                "started_at": 1_700_000_000.0,
                "boot_id": "a-boot-that-is-not-ours",
                "pid": 999,
            }
        )
    )

    record = read_history(None, "scout-delegate-ghost01")
    assert record is not None
    assert record["status"] == "interrupted"
    assert record["task"] == "a task nobody finished"


def test_history_is_newest_first_and_filterable_by_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")
    _write_agent(tmp_path, "quant")

    old = _run_delegation(monkeypatch, tmp_path, task="the older one")
    new = _run_delegation(monkeypatch, tmp_path, task="the newer one")
    other = _run_delegation(monkeypatch, tmp_path, slug="quant", task="someone else's")
    delegate_module._delegations.clear()

    ids = [r["task_id"] for r in list_history(user_id=USER)]
    assert set(ids) == {old.task_id, new.task_id, other.task_id}
    assert ids.index(new.task_id) < ids.index(old.task_id)

    assert len(list_history(user_id=USER, limit=1)) == 1
    assert [r["task_id"] for r in list_history(user_id=USER, agent_slug="quant")] == [
        other.task_id
    ]


def test_a_task_id_can_never_walk_out_of_the_delegations_directory(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")
    (tmp_path / "secrets.md").write_text("# Delegation secrets\n\n- **Status:** done\n")

    assert read_history(None, "../../secrets") is None
    assert read_history_events(None, "../../secrets") == ([], "")
    assert read_history(USER, "../../secrets") is None


# ── PERF-204: what the list route is allowed to read ───────────────────────
# The dashboard re-lists every 5 seconds while a delegation runs, and nothing
# prunes these directories, so two things have to hold: a record whose status
# file is the whole record never opens its transcript, and a listing opens at
# most `limit` of them. Neither may cost freshness -- a record that changed on
# disk must never be answered from something remembered.


def _spy_on_transcript_reads(monkeypatch):
    """Collects every delegation transcript opened from here on.

    Both shapes count: ``{task_id}/transcript.md`` and the flat, agent-keyed
    ``delegations/{task_id}.md``. Reads still happen; this only watches.
    """
    real = Path.read_text
    opened: list[Path] = []

    def spy(self, *args, **kwargs):
        if self.name == DELEGATION_TRANSCRIPT_FILENAME or (
            self.suffix == ".md" and self.parent.name == "delegations"
        ):
            opened.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    return opened


@pytest.fixture(autouse=True)
def _clean_markdown_cache():
    history_module._MD_CACHE.clear()
    yield
    history_module._MD_CACHE.clear()


def test_a_current_record_never_opens_its_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")

    dt = _run_delegation(monkeypatch, tmp_path)
    delegate_module._delegations.clear()  # the restart
    assert (paths.delegation_dir(USER, dt.task_id) / "transcript.md").is_file()

    opened = _spy_on_transcript_reads(monkeypatch)
    row = list_history(user_id=USER)[0]
    record = read_history(USER, dt.task_id)

    assert opened == []  # status.json already carries the whole record
    assert row["task"] == "scan SOL pools"
    assert row["status"] == "done"
    assert record["result"] == "three pools worth watching"
    assert record["tool_count"] == 1
    assert record["server_name"] == "local"


def test_a_pre_feat035_record_still_backfills_from_its_transcript(
    tmp_path, monkeypatch
):
    """State and provenance only: the task and the result are in the markdown."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")
    directory = _delegations_dir(tmp_path, "scout")
    (directory / "scout-delegate-legacy02.status.json").write_text(
        json.dumps(
            {
                "state": "done",
                "task_id": "scout-delegate-legacy02",
                "agent_slug": "scout",
                "boot_id": "a-boot-that-is-not-ours",
                "pid": 999,
            }
        )
    )
    (directory / "scout-delegate-legacy02.md").write_text(LEGACY_MD)

    opened = _spy_on_transcript_reads(monkeypatch)
    record = read_history(None, "scout-delegate-legacy02")

    assert [p.name for p in opened] == ["scout-delegate-legacy02.md"]
    assert record["task"] == "check the SOL pools"
    assert record["result"] == "three pools worth watching"
    assert record["tool_count"] == 7
    assert record["status"] == "done"
    assert record["server_name"] == "local"


def test_a_delegation_that_changed_is_never_served_from_stale_state(
    tmp_path, monkeypatch
):
    """The status file is the record, so it is read fresh on every call."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")

    dt = _run_delegation(monkeypatch, tmp_path)
    delegate_module._delegations.clear()
    assert list_history(user_id=USER)[0]["status"] == "done"

    # The same task, run again and this time failing: the transcript on disk
    # still tells the old story, and must not be the one that gets told.
    status_path = paths.delegation_dir(USER, dt.task_id) / "status.json"
    data = json.loads(status_path.read_text())
    data.update(state="error", result="", error="the second run blew up", tool_count=9)
    status_path.write_text(json.dumps(data))

    row = list_history(user_id=USER)[0]
    assert row["status"] == "error"
    assert row["tool_count"] == 9
    record = read_history(USER, dt.task_id)
    assert record["error"] == "the second run blew up"
    assert record["result"] == ""  # not resurrected from the old transcript

    # And one that is gone is gone, on the very next call.
    shutil.rmtree(paths.delegation_dir(USER, dt.task_id))
    assert list_history(user_id=USER) == []
    assert read_history(USER, dt.task_id) is None


def test_a_rewritten_legacy_transcript_is_parsed_again(tmp_path, monkeypatch):
    """The parse is memoized on (mtime, size); a changed file is a cache miss."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")
    md = _delegations_dir(tmp_path, "scout") / "scout-delegate-legacy01.md"
    md.write_text(LEGACY_MD)

    assert read_history(None, "scout-delegate-legacy01")["result"] == (
        "three pools worth watching"
    )

    # A different length: the size changes.
    md.write_text(LEGACY_MD.replace("three pools worth watching", "nothing to see"))
    assert read_history(None, "scout-delegate-legacy01")["result"] == "nothing to see"

    # The same length: only the mtime changes.
    md.write_text(LEGACY_MD.replace("three pools worth watching", "three ponds, damp"))
    stamp = md.stat().st_mtime + 10
    os.utime(md, (stamp, stamp))
    assert (
        read_history(None, "scout-delegate-legacy01")["result"] == "three ponds, damp"
    )


def test_a_listing_hydrates_only_the_page_it_returns(tmp_path, monkeypatch):
    """80 delegations on disk, a page of 5: five transcripts opened, not eighty."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")

    legacy = _delegations_dir(tmp_path, "scout")
    for i in range(40):
        md = legacy / f"scout-delegate-old{i:02d}.md"
        md.write_text(LEGACY_MD)
        os.utime(md, (1_700_000_000 + i, 1_700_000_000 + i))

    for i in range(40):
        record_dir = paths.delegation_dir(USER, f"scout-delegate-new{i:02d}")
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / "status.json").write_text(
            json.dumps(
                {
                    "state": "done",
                    "task_id": f"scout-delegate-new{i:02d}",
                    "agent_slug": "scout",
                    "user_id": USER,
                    "task": "an older, fully recorded delegation",
                    "result": "done",
                    "error": "",
                    "tool_count": 3,
                    "started_at": 1_600_000_000 + i,
                }
            )
        )
        (record_dir / "transcript.md").write_text(LEGACY_MD)

    opened = _spy_on_transcript_reads(monkeypatch)
    rows = list_history(user_id=None, limit=5)

    assert len(rows) == 5
    # The five newest are the markdown-only records, so five is also the most
    # transcripts this page could possibly need -- and it opens exactly those.
    assert len(opened) == 5
    assert [r["task_id"] for r in rows] == [
        f"scout-delegate-old{i:02d}" for i in (39, 38, 37, 36, 35)
    ]


def test_the_no_server_placeholder_does_not_reach_the_wire(tmp_path, monkeypatch):
    """``- **Server:** -`` means "no server", not a server literally named "-"."""
    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path))
    _write_agent(tmp_path, "scout")
    md = _delegations_dir(tmp_path, "scout") / "scout-delegate-legacy03.md"
    md.write_text(LEGACY_MD.replace("- **Server:** local", "- **Server:** -"))

    assert read_history(None, "scout-delegate-legacy03")["server_name"] is None
