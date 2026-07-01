"""Unit tests for the emergency-shutdown feature (FEAT-007).

Covers the declarative ``shutdown.md`` policy loader and its strategy → agent →
default resolution.
"""

from condor.agents import shutdown as shutdown_module
from condor.agents import strategy as strategy_module
from condor.agents.shutdown import (
    DEFAULT_POLICY,
    POLICY_FLATTEN_ALL,
    POLICY_KEEP_ALL,
    ShutdownPolicy,
    load_shutdown_policy,
)
from condor.agents.strategy import Strategy


def _make_strategy(tmp_path, monkeypatch) -> Strategy:
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    s = Strategy(agent_slug="acme", name="Scalper")
    s.dir.mkdir(parents=True, exist_ok=True)
    return s


def _write_shutdown_md(path, policy: str, cancel: bool = True, body: str = "Body.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\non_kill_switch: {policy}\ncancel_open_orders: {str(cancel).lower()}\n---\n\n{body}\n"
    )


# ── ShutdownPolicy.from_dict ──


def test_policy_from_dict_valid():
    p = ShutdownPolicy.from_dict(
        {"on_kill_switch": POLICY_FLATTEN_ALL, "cancel_open_orders": False}
    )
    assert p.on_kill_switch == POLICY_FLATTEN_ALL
    assert p.cancel_open_orders is False


def test_policy_from_dict_unknown_falls_back_to_default():
    p = ShutdownPolicy.from_dict({"on_kill_switch": "nuke_everything"})
    assert p.on_kill_switch == DEFAULT_POLICY  # keep_spot_close_perp
    assert p.cancel_open_orders is True  # default


def test_policy_from_dict_empty_is_default():
    p = ShutdownPolicy.from_dict({})
    assert p.on_kill_switch == DEFAULT_POLICY


# ── load_shutdown_policy resolution ──


def test_resolution_prefers_strategy_over_agent_over_default(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    agent_dir = s.dir.parent.parent  # {root}/acme
    defaults_dir = tmp_path / "_defaults"

    _write_shutdown_md(defaults_dir / "shutdown.md", DEFAULT_POLICY, body="default body")
    _write_shutdown_md(agent_dir / "shutdown.md", POLICY_KEEP_ALL, body="agent body")
    _write_shutdown_md(s.dir / "shutdown.md", POLICY_FLATTEN_ALL, body="strategy body")

    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == POLICY_FLATTEN_ALL
    assert body == "strategy body"


def test_resolution_falls_back_to_agent(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    agent_dir = s.dir.parent.parent
    _write_shutdown_md(tmp_path / "_defaults" / "shutdown.md", DEFAULT_POLICY)
    _write_shutdown_md(agent_dir / "shutdown.md", POLICY_KEEP_ALL, body="agent body")

    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == POLICY_KEEP_ALL
    assert body == "agent body"


def test_resolution_falls_back_to_default_file(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    _write_shutdown_md(
        tmp_path / "_defaults" / "shutdown.md", POLICY_FLATTEN_ALL, body="default body"
    )
    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == POLICY_FLATTEN_ALL
    assert body == "default body"


def test_resolution_no_files_returns_builtin_default(tmp_path, monkeypatch):
    s = _make_strategy(tmp_path, monkeypatch)
    policy, body = load_shutdown_policy(s)
    assert policy.on_kill_switch == DEFAULT_POLICY
    assert body == ""
