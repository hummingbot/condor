"""SEC-657: tick snapshots redact credential-named tool-call arguments.

Snapshots are served to every dashboard user and to any journal seat, and a
tick can run a routine whose Config carries ``api_key``/``password`` fields.
``journal.render_tool_calls`` is the one place snapshot tool calls are
formatted, so the shared ``conversations._redact`` is applied there once.
"""

from __future__ import annotations

import inspect
import re

from condor.agents import engine, journal
from condor.agents.journal import JournalManager, save_experiment_snapshot
from condor.runtime.conversations import REDACTED

_SECRET = "sk-live-123"


def _routine_call() -> dict:
    return {
        "name": "manage_routines",
        "status": "completed",
        "input": {
            "action": "run",
            "config": {"api_key": _SECRET, "trading_pair": "SOL-USDC"},
        },
    }


def _assert_redacted(text: str) -> None:
    assert _SECRET not in text
    assert REDACTED in text
    assert '"trading_pair": "SOL-USDC"' in text
    assert '"action": "run"' in text


def test_full_snapshot_redacts_nested_secret_keys(tmp_path):
    call = _routine_call()
    path = JournalManager("a", session_dir=tmp_path).save_full_snapshot(
        tick=1,
        timestamp="t",
        system_prompt="p",
        response_text="r",
        tool_calls=[call],
        executors_data="",
        risk_state={},
        duration=0.1,
    )
    assert path == tmp_path / "snapshots" / "snapshot_1.md"
    _assert_redacted(path.read_text())
    # The caller's folded list is not mutated (actions log still reads it).
    assert call["input"]["config"]["api_key"] == _SECRET


def test_experiment_snapshot_redacts_nested_secret_keys(tmp_path):
    path = save_experiment_snapshot(
        agent_dir=tmp_path,
        experiment_num=1,
        execution_mode="dry_run",
        timestamp="t",
        system_prompt="p",
        response_text="r",
        tool_calls=[_routine_call()],
        executors_data="",
        risk_state={},
        duration=0.1,
    )
    assert path == tmp_path / "dry_runs" / "experiment_1.md"
    _assert_redacted(path.read_text())


def test_a_string_input_is_rendered_unchanged():
    rendered = journal.render_tool_calls(
        [{"name": "run_code", "status": "completed", "input": "print('hi')"}]
    )
    assert "**Input:**\n```json\nprint('hi')\n```" in rendered


def test_the_secret_hint_list_lives_only_in_conversations():
    for module in (journal, engine):
        src = inspect.getsource(module)
        assert "_SECRET_KEY_HINTS" not in src
        assert not re.search(r'"passphrase"|"mnemonic"', src)
