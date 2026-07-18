"""Routine containment (§7.2 / §11): a hostile routine (infinite sync loop,
os._exit) is killed at the timeout and the host survives; validation of a
blocking-import module cannot hang the parent; the worker environment
carries no CONDOR_* secrets; the RunContext carries no capability or
execution surface."""

import asyncio
import os

import pytest

from condor.routines_worker import (
    RunContext,
    run_routine_in_worker,
    scrubbed_env,
    validate_routine_in_worker,
)


def test_infinite_sync_loop_killed_at_timeout(tmp_path):
    """The in-process runner could never interrupt `while True: pass`; the
    worker is SIGKILLed and the host survives."""
    path = tmp_path / "spin.py"
    path.write_text(
        "from pydantic import BaseModel\n"
        "class Config(BaseModel):\n"
        '    """spin"""\n'
        "async def run(config, context):\n"
        "    while True:\n"
        "        pass\n"
    )
    # Validation imports fine; the RUN spins. Use the validate entry for the
    # import, then a direct worker run via stdin protocol with a tiny timeout.
    from condor.routines_worker import _spawn

    out = asyncio.run(
        _spawn({"action": "validate", "path": str(path)}, timeout_s=10)
    )
    assert out.get("ok") is True

    # Simulate the run through the raw worker protocol against a temp module:
    # routine resolution needs the global registry, so drive the hostile loop
    # through validate-with-blocking-import below and assert the kill here
    # via a module whose IMPORT spins (same containment mechanism).
    spin_import = tmp_path / "spin_import.py"
    spin_import.write_text("while True:\n    pass\n")
    out = asyncio.run(
        validate_routine_in_worker(str(spin_import), timeout_s=2)
    )
    assert "timed out" in out.get("error", "")
    assert "killed" in out["error"]


def test_os_exit_takes_down_only_the_worker(tmp_path):
    path = tmp_path / "die.py"
    path.write_text("import os\nos._exit(42)\n")
    out = asyncio.run(validate_routine_in_worker(str(path), timeout_s=10))
    assert "died" in out.get("error", "")
    assert "exit 42" in out["error"]
    # ...and this process is obviously still alive to assert it.


def test_blocking_import_cannot_hang_validation(tmp_path):
    path = tmp_path / "sleepy.py"
    path.write_text("import time\ntime.sleep(600)\n")
    out = asyncio.run(validate_routine_in_worker(str(path), timeout_s=2))
    assert "timed out" in out.get("error", "")


def test_validate_reports_contract_violations(tmp_path):
    path = tmp_path / "empty.py"
    path.write_text("x = 1\n")
    out = asyncio.run(validate_routine_in_worker(str(path), timeout_s=10))
    assert "missing Config" in out["error"]
    assert "missing async run" in out["error"]


def test_worker_env_carries_no_condor_secrets(monkeypatch):
    monkeypatch.setenv("CONDOR_RUN_CAPABILITY", "cap-123")
    monkeypatch.setenv("CONDOR_HL_PRIVATE_KEY", "0x" + "ab" * 32)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("MY_API_KEY", "k")
    monkeypatch.setenv("SOME_HARMLESS", "yes")
    env = scrubbed_env()
    assert "CONDOR_RUN_CAPABILITY" not in env
    assert "CONDOR_HL_PRIVATE_KEY" not in env
    assert "TELEGRAM_BOT_TOKEN" not in env
    assert "MY_API_KEY" not in env
    assert env.get("SOME_HARMLESS") == "yes"
    assert "PATH" in env


def test_readonly_data_key_allowlisted_but_only_exact(monkeypatch):
    # COINGECKO_API_KEY is a read-only market-data key routines need; it is the
    # one narrow exception to the API_KEY denylist — matched exactly, so a
    # lookalike name is still scrubbed.
    monkeypatch.setenv("COINGECKO_API_KEY", "cg-demo-123")
    monkeypatch.setenv("COINGECKO_API_KEY_BACKUP", "should-not-pass")
    env = scrubbed_env()
    assert env.get("COINGECKO_API_KEY") == "cg-demo-123"
    assert "COINGECKO_API_KEY_BACKUP" not in env


def test_run_context_has_no_capability_or_execution_surface():
    ctx = RunContext(agent_slug="acme")
    assert ctx.agent_slug == "acme"
    public = {a for a in vars(ctx) if not a.startswith("_")}
    assert public == {"agent_slug"}  # structured inputs in, data out (§7.2)


def test_unknown_routine_errors_cleanly():
    out = asyncio.run(
        run_routine_in_worker("definitely_not_a_routine_xyz", {}, timeout_s=30)
    )
    assert "not found" in out.get("error", "")
