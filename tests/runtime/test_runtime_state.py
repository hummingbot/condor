"""Tests for the runtime scratch state and the timeout policy (FEAT-013).

State is deliberately the weakest of the three persistence layers: memory is
curated and durable, the journal is an append-only narrative, and this is a
cursor store. The tests pin that distinction where it matters — namespace
isolation, JSON-only values, and expiry.
"""

import json
import os
import time

import pytest

from condor.runtime import state as state_module
from condor.runtime.state import (
    BoundState,
    NamespaceError,
    cleanup_orphans,
    clear_state,
    flush_all,
    get_state,
    list_state,
    load_namespace,
    namespace_for,
    set_state,
)
from condor.runtime.timeouts import TimeoutPolicy, resolve_tick_timeout

NS = "brigado.mm"
OTHER = "brigado.other"


@pytest.fixture
def state_root(tmp_path, monkeypatch):
    """Isolated on-disk root and a clean in-memory cache."""
    monkeypatch.setattr("condor.agents.agent._DATA_ROOT", tmp_path)
    monkeypatch.setattr(state_module, "_cache", {})
    monkeypatch.setattr(state_module, "_last_write", {})
    monkeypatch.setattr(state_module, "_dirty", set())
    # Writes land under the strategy dir when it exists.
    (tmp_path / "brigado" / "strategies" / "mm").mkdir(parents=True)
    (tmp_path / "brigado" / "strategies" / "other").mkdir(parents=True)
    return tmp_path


# ── Core behavior ──


def test_set_get_roundtrip(state_root):
    set_state(NS, "cursor", "exec-42")
    set_state(NS, "count", 7)
    set_state(NS, "nested", {"a": [1, 2]})

    assert get_state(NS, "cursor") == "exec-42"
    assert get_state(NS, "count") == 7
    assert get_state(NS, "nested") == {"a": [1, 2]}
    assert get_state(NS, "missing", "fallback") == "fallback"
    assert list_state(NS) == {
        "cursor": "exec-42",
        "count": 7,
        "nested": {"a": [1, 2]},
    }


def test_namespace_isolation(state_root):
    """One strategy cannot read another's cursors by guessing a key."""
    set_state(NS, "cursor", "mine")
    set_state(OTHER, "cursor", "theirs")

    assert get_state(NS, "cursor") == "mine"
    assert get_state(OTHER, "cursor") == "theirs"
    assert list_state(NS) == {"cursor": "mine"}

    # A bound view cannot be talked out of its namespace.
    bound = BoundState(NS)
    assert bound.get("cursor") == "mine"
    assert bound.namespace == NS


def test_rejects_unsafe_namespace(state_root):
    """A namespace maps to a directory, so it must not escape one."""
    for bad in ("../escape", "a/b", "", "with space"):
        with pytest.raises(NamespaceError):
            set_state(bad, "k", 1)


def test_expiry(state_root):
    """An expired key reads as absent and drops out of the listing."""
    set_state(NS, "cooldown", "on", expires_in=-1)  # already past
    assert get_state(NS, "cooldown", "gone") == "gone"
    assert "cooldown" not in list_state(NS)

    set_state(NS, "live", "yes", expires_in=60)
    assert get_state(NS, "live") == "yes"
    assert "live" in list_state(NS)


def test_non_serializable_raises(state_root):
    """A value that cannot be persisted fails now, not silently at flush."""
    from datetime import datetime

    with pytest.raises(TypeError) as exc:
        set_state(NS, "when", datetime.now())

    message = str(exc.value)
    assert "JSON-serializable" in message
    assert "datetime" in message
    # And nothing was stored.
    assert get_state(NS, "when") is None


def test_clear(state_root):
    set_state(NS, "a", 1)
    set_state(NS, "b", 2)

    assert clear_state(NS, "a") == 1
    assert clear_state(NS, "missing") == 0
    assert list_state(NS) == {"b": 2}

    assert clear_state(NS) == 1
    assert list_state(NS) == {}


# ── Persistence ──


def test_persistence_across_reload(state_root):
    """Dropping the in-memory cache and rehydrating gets the value back."""
    set_state(NS, "cursor", "exec-99")
    flush_all()

    state_module._cache.clear()
    assert load_namespace(NS) == 1
    assert get_state(NS, "cursor") == "exec-99"

    # And it landed under the strategy it belongs to, not a global blob.
    written = state_root / "brigado" / "strategies" / "mm" / "state.json"
    assert written.is_file()
    assert json.loads(written.read_text())["entries"]["cursor"]["value"] == "exec-99"


def test_debounced_writes(state_root, monkeypatch):
    """Rapid sets do not hammer the disk; the value is still correct in memory."""
    writes = []
    real_write = state_module.write_status

    def counting_write(session_dir, filename=None, **fields):
        writes.append(filename)
        return real_write(session_dir, filename, **fields)

    monkeypatch.setattr(state_module, "write_status", counting_write)

    for i in range(100):
        set_state(NS, "cursor", i)

    # 100 sets, far fewer writes (the first one, then debounced).
    assert len(writes) < 10
    assert get_state(NS, "cursor") == 99

    # A forced flush always writes the latest value out.
    flush_all()
    state_module._cache.clear()
    load_namespace(NS)
    assert get_state(NS, "cursor") == 99


def test_cleanup_orphans(state_root):
    """State for a deleted strategy is forgotten."""
    set_state(NS, "a", 1)
    set_state(OTHER, "b", 2)

    import shutil

    shutil.rmtree(state_root / "brigado" / "strategies" / "other")

    assert cleanup_orphans(agents_root=state_root) == 1
    assert NS in state_module._cache
    assert OTHER not in state_module._cache


def test_namespace_for_uses_agent_and_strategy():
    """Keyed on (agent, strategy), NOT the session — a cursor outlives a session."""
    from types import SimpleNamespace

    engine = SimpleNamespace(
        agent=SimpleNamespace(slug="brigado"),
        strategy=SimpleNamespace(slug="mm"),
        session_num=7,
    )
    assert namespace_for(engine) == "brigado.mm"


# ── Timeout policy ──


def test_timeout_defaults():
    policy = TimeoutPolicy()
    assert policy.prompt_lock == 30
    assert policy.prompt_overall == 1800
    assert policy.confirmation == 120


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("CONDOR_TIMEOUT_PROMPT_OVERALL", "60")
    monkeypatch.setenv("CONDOR_TIMEOUT_MCP_CALL", "2.5")

    policy = TimeoutPolicy.load()

    assert policy.prompt_overall == 60
    assert policy.mcp_call == 2.5
    # Untouched fields keep their defaults.
    assert policy.prompt_lock == 30


def test_timeout_bad_override_is_ignored(monkeypatch):
    """A typo in an env var must not stop the process from starting."""
    monkeypatch.setenv("CONDOR_TIMEOUT_PROMPT_OVERALL", "not-a-number")
    assert TimeoutPolicy.load().prompt_overall == 1800


def test_resolve_tick_timeout_precedence():
    """Caller override beats strategy config beats the default."""
    assert resolve_tick_timeout("loop", caller=42, strategy=99) == 42
    assert resolve_tick_timeout("loop", caller=None, strategy=99) == 99
    assert resolve_tick_timeout("loop") == TimeoutPolicy().tick_default


def test_timeouts_are_sourced_from_the_policy():
    """The migrated call sites read the policy instead of their own literal."""
    from condor.runtime import sessions
    from condor.runtime.confirmations import CONFIRMATION_TIMEOUT
    from condor.runtime.timeouts import TIMEOUTS

    assert sessions.PROMPT_LOCK_TIMEOUT == TIMEOUTS.prompt_lock
    assert sessions.PROMPT_OVERALL_TIMEOUT == TIMEOUTS.prompt_overall
    assert CONFIRMATION_TIMEOUT == TIMEOUTS.confirmation
