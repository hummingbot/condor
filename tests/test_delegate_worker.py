"""The background Condor worker seat (FEAT-032).

The chat and the routine-authoring worker are the SAME agent record since
FEAT-033, so one flag — ``--delegate-worker`` — is the only thing that tells the
subprocess which seat it is sitting in. It carries two jobs, and both are covered
here: it selects the worker framing (authoring is yours, you have no user), and
it makes ``delegate(action="start")`` refuse to recurse. The refusal is in code on
purpose: an unattended session auto-approves its tool calls, so "we told it not
to" is not a bound on fan-out.
"""

import asyncio

import pytest

from condor.agents import agent as agent_module
from condor.agents import strategy as strategy_module


def _write_agent(root, slug, *, name):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(f"---\nname: {name}\n---\n\nBody.\n")
    return d


@pytest.fixture
def settings_obj(monkeypatch):
    from mcp_servers.condor.settings import settings

    monkeypatch.setattr(settings, "agent_slug", "")
    monkeypatch.setattr(settings, "delegate_worker", False)
    return settings


def _instructions(settings_obj, monkeypatch, *, slug="", worker=False) -> str:
    from mcp_servers.condor import server

    monkeypatch.setattr(settings_obj, "agent_slug", slug)
    monkeypatch.setattr(settings_obj, "delegate_worker", worker)
    return server._build_instructions()


# ── framing: three seats, one record ──


def test_worker_instructions_own_the_authoring_and_close_delegation(
    settings_obj, monkeypatch
):
    text = _instructions(settings_obj, monkeypatch, slug="condor", worker=True)

    assert text.startswith("You are a BACKGROUND WORKER instance of Condor")
    # The reason it was started: it authors, it does not route the work onward.
    assert "ROUTINE AUTHORING IS YOURS" in text
    assert "routine_cookbook" in text
    # Into the GLOBAL library — an agent-private dir would hide the result from
    # the very user who asked for it.
    assert "GLOBAL" in text and "no `strategy_id`" in text
    assert "TEST it with" in text
    # And it never spawns another background agent.
    assert 'NEVER start another delegation: `delegate(action="start", ...)`' in text
    assert 'delegate(action="start", agent="condor"' not in text


def test_worker_keeps_the_coordinator_routing_below_its_own_framing(
    settings_obj, monkeypatch
):
    """It is still Condor: same skills/agents priority, one branch swapped."""
    text = _instructions(settings_obj, monkeypatch, slug="condor", worker=True)
    assert "Condor exposes reusable **skills**" in text
    assert "ROUTING RULE" in text
    assert 'manage_skill(action="list")' in text


def test_chat_instructions_are_untouched_by_the_flag(settings_obj, monkeypatch):
    """Same record, flag off — the interactive chat text must not drift."""
    chat = _instructions(settings_obj, monkeypatch, slug="condor", worker=False)
    assert chat.startswith("Condor exposes reusable **skills**")
    assert "BACKGROUND WORKER" not in chat
    assert "ROUTINE AUTHORING IS YOURS" not in chat


def test_a_specialist_is_never_re_framed_as_a_condor_worker(
    settings_obj, monkeypatch, tmp_path
):
    """A specialist already reads `_agent_base`; the flag must not override it."""
    from condor.agents.agent import identity_header

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, "backpack_mm", name="Backpack MM")

    text = _instructions(settings_obj, monkeypatch, slug="backpack_mm", worker=True)
    assert identity_header("backpack_mm", "Backpack MM") in text
    assert "BACKGROUND WORKER" not in text


# ── the guard: refusal lives in code, not in the prompt ──


def test_worker_cannot_start_a_nested_delegation(settings_obj, monkeypatch):
    from mcp_servers.condor.tools import delegate as delegate_tool

    monkeypatch.setattr(settings_obj, "delegate_worker", True)
    result = asyncio.run(
        delegate_tool.delegate(action="start", agent="brigado", task="go do a thing")
    )
    assert "error" in result
    assert "Nested delegation refused" in result["error"]
    assert "task_id" not in result


def test_worker_may_still_poll_an_existing_delegation(settings_obj, monkeypatch):
    """The guard closes fan-out, not visibility — get/list stay open."""
    from mcp_servers.condor.tools import delegate as delegate_tool

    monkeypatch.setattr(settings_obj, "delegate_worker", True)
    calls = []

    async def fake_call(method, path, payload=None):
        calls.append((method, path))
        return {"ok": True}

    monkeypatch.setattr(delegate_tool, "call_main_api", fake_call)

    assert asyncio.run(delegate_tool.delegate(action="get", task_id="t1")) == {
        "ok": True
    }
    assert asyncio.run(delegate_tool.delegate(action="list")) == {"ok": True}
    assert calls == [("GET", "/agents/delegations/t1"), ("GET", "/agents/delegations")]


def test_the_chat_can_still_start_delegations(settings_obj, monkeypatch):
    from mcp_servers.condor.tools import delegate as delegate_tool

    async def fake_call(method, path, payload=None):
        return {"task_id": "condor-delegate-abc", "status": "running"}

    monkeypatch.setattr(delegate_tool, "call_main_api", fake_call)
    result = asyncio.run(
        delegate_tool.delegate(
            action="start", agent="condor", task="build a routine that scans SOL pools"
        )
    )
    assert result["task_id"] == "condor-delegate-abc"
    assert "next_steps" in result


# ── the flag reaches the subprocess ──


def _condor_args(monkeypatch, **kwargs) -> list[str]:
    import config_manager
    from handlers.agents._shared import build_mcp_servers_for_session

    class _NoServers:
        def get_accessible_servers(self, user_id):
            return []

        def get_server(self, name):
            return None

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _NoServers())
    monkeypatch.setattr(config_manager, "get_effective_server", lambda *a, **k: None)
    servers = build_mcp_servers_for_session(42, 42, **kwargs)
    return next(s for s in servers if s["name"] == "condor")["args"]


def test_flag_is_appended_only_when_the_session_is_a_worker(monkeypatch):
    assert "--delegate-worker" in _condor_args(
        monkeypatch, agent_slug="condor", delegate_worker=True
    )
    # The chat's own subprocess carries --agent-slug condor too (FEAT-033) — the
    # flag is what separates them.
    assert "--delegate-worker" not in _condor_args(monkeypatch, agent_slug="condor")
    assert "--delegate-worker" not in _condor_args(monkeypatch)


def test_settings_parse_the_flag_off_by_default(monkeypatch):
    import sys

    from mcp_servers.condor import settings as settings_module

    monkeypatch.setattr(sys, "argv", ["prog", "--agent-slug", "condor"])
    assert settings_module._parse_settings().delegate_worker is False

    monkeypatch.setattr(
        sys, "argv", ["prog", "--agent-slug", "condor", "--delegate-worker"]
    )
    assert settings_module._parse_settings().delegate_worker is True


# ── only Condor's own delegations get the worker seat ──


def _delegated_worker_kwarg(monkeypatch, tmp_path, slug: str) -> bool:
    """Run a delegation for ``slug`` and report the flag it built its tools with."""
    from condor.acp import client as acp_client_module
    from condor.agents.consult import _run_agent_to_completion

    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(strategy_module, "_DATA_ROOT", tmp_path)
    _write_agent(tmp_path, slug, name=slug.title())

    seen: dict = {}

    def fake_build(*args, **kwargs):
        seen.update(kwargs)
        return []

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            pass

        async def stop(self):
            pass

        async def prompt(self, text):
            return "done"

    monkeypatch.setattr(
        "handlers.agents._shared.build_mcp_servers_for_session", fake_build
    )
    monkeypatch.setattr(
        "handlers.agents._shared.build_agent_context", lambda *a, **k: "prompt"
    )
    monkeypatch.setattr(acp_client_module, "ACPClient", _FakeClient)

    asyncio.run(
        _run_agent_to_completion(
            slug=slug,
            user_id=42,
            chat_id=42,
            server_name=None,
            task="build a routine",
            delegate_worker=True,
        )
    )
    return seen["delegate_worker"]


def test_condor_delegation_gets_the_worker_seat(monkeypatch, tmp_path):
    assert _delegated_worker_kwarg(monkeypatch, tmp_path, "condor") is True


def test_specialist_delegation_is_left_exactly_as_it_was(monkeypatch, tmp_path):
    """`_agent_base` invites an agent to delegate long work on; don't contradict it."""
    assert _delegated_worker_kwarg(monkeypatch, tmp_path, "backpack_mm") is False
