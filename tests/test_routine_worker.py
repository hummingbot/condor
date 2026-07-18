"""Routine containment (§7.2 / §11): a hostile routine (infinite sync loop,
os._exit) is killed at the timeout and the host survives; validation of a
blocking-import module cannot hang the parent; the worker environment is the
full operator environment (process env + repo .env — fault containment, not
secrecy); the RunContext carries no capability or execution surface."""

import asyncio
import os

import pytest

from condor.routines_worker import (
    RunContext,
    run_routine_in_worker,
    validate_routine_in_worker,
    worker_env,
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


def test_worker_env_is_process_env_plus_repo_dotenv(tmp_path, monkeypatch):
    """Routines see everything the operator configured: full process env,
    with repo .env keys merged UNDER it (a live env var wins)."""
    import condor.routines_worker as rw

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "CONDOR_SOLANA_RPC=https://rpc.example/abc\n"
        "FROM_FILE_ONLY='quoted'\n"
        "OVERRIDDEN=file-value\n"
    )
    monkeypatch.setattr(rw, "_repo_env_file", lambda: _parse_env(env_file))
    monkeypatch.setenv("OVERRIDDEN", "live-value")
    monkeypatch.setenv("SOME_HARMLESS", "yes")

    env = worker_env()
    assert env.get("CONDOR_SOLANA_RPC") == "https://rpc.example/abc"
    assert env.get("FROM_FILE_ONLY") == "quoted"
    assert env.get("OVERRIDDEN") == "live-value"  # process env wins
    assert env.get("SOME_HARMLESS") == "yes"
    assert "PATH" in env


def _parse_env(path):
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip("'\"")
    return out


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
