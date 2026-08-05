"""Unit tests for delegation history -- the disk-backed read side (FEAT-035).

The live registry dies with the process; these tests cover what survives it:
the record rebuilt from ``{task_id}.status.json``, the transcript rebuilt from
``{task_id}.events.json``, the markdown fallback for records written before
either file existed, and the "the process died mid-task" reconciliation.
"""

import asyncio
import json

import pytest

from condor.agents import agent as agent_module
from condor.agents import consult as consult_module
from condor.agents import delegate as delegate_module
from condor.agents.delegate import start_delegation
from condor.agents.delegation_history import (
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
            user_id=7,
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
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    dt = _run_delegation(monkeypatch, tmp_path)
    delegate_module._delegations.clear()  # the restart

    record = read_history(dt.task_id)
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

    assert [r["task_id"] for r in list_history()] == [dt.task_id]


def test_transcript_survives_with_the_same_shape_the_wire_uses(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")

    dt = _run_delegation(monkeypatch, tmp_path)
    sidecar = tmp_path / "scout" / "delegations" / f"{dt.task_id}.events.json"
    assert json.loads(sidecar.read_text())["events"] == delegate_module.events_for_wire(
        dt.events
    )

    delegate_module._delegations.clear()
    events, markdown = read_history_events(dt.task_id)
    assert markdown == ""  # structured events win; no fallback needed
    assert [e["type"] for e in events] == ["thought", "tool"]
    tool = events[1]
    assert tool["name"] == "get_market_data"
    assert tool["status"] == "completed"
    assert tool["output"] == "ok"


def test_legacy_transcript_without_a_status_file_is_still_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")
    (_delegations_dir(tmp_path, "scout") / "scout-delegate-legacy01.md").write_text(
        LEGACY_MD
    )

    record = read_history("scout-delegate-legacy01")
    assert record is not None
    assert record["status"] == "done"
    assert record["agent"] == "scout"
    assert record["task"] == "check the SOL pools"
    assert record["result"] == "three pools worth watching"
    assert record["tool_count"] == 7
    # Nobody's, so nobody but an admin can see it -- the route relies on this.
    assert record["user_id"] == 0

    events, markdown = read_history_events("scout-delegate-legacy01")
    assert events == []
    assert "🔧 **1. get_market_data**" in markdown


def test_a_task_the_process_died_on_reads_as_interrupted(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
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

    record = read_history("scout-delegate-ghost01")
    assert record is not None
    assert record["status"] == "interrupted"
    assert record["task"] == "a task nobody finished"


def test_history_is_newest_first_and_filterable_by_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")
    _write_agent(tmp_path, "quant")

    old = _run_delegation(monkeypatch, tmp_path, task="the older one")
    new = _run_delegation(monkeypatch, tmp_path, task="the newer one")
    other = _run_delegation(monkeypatch, tmp_path, slug="quant", task="someone else's")
    delegate_module._delegations.clear()

    ids = [r["task_id"] for r in list_history()]
    assert set(ids) == {old.task_id, new.task_id, other.task_id}
    assert ids.index(new.task_id) < ids.index(old.task_id)

    assert len(list_history(limit=1)) == 1
    assert [r["task_id"] for r in list_history(agent_slug="quant")] == [other.task_id]


def test_a_task_id_can_never_walk_out_of_the_delegations_directory(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "scout")
    (tmp_path / "secrets.md").write_text("# Delegation secrets\n\n- **Status:** done\n")

    assert read_history("../../secrets") is None
    assert read_history_events("../../secrets") == ([], "")
