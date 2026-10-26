"""A dry run cannot execute Python, whoever is driving the bridge (CORR-748).

SEC-616 named ``run_code`` in ``dry_run_refusal`` and SEC-626 named
``manage_routines`` beside it, and both were covered by calling the policy
directly. QA then watched a dry run execute a snippet and fetch live candles on
a running system, with that policy intact and those tests passing (CO-2).

The gap is the layer, not the policy. ``dry_run_refusal`` is consulted from
``risk.auto_approve_with_risk_check``, reached from
``ACPClient._on_request_permission`` — a handler for a request the *bridge*
chooses to send. ``claude-agent-acp`` sends it; a bridge that auto-approves its
own tool calls, or implements no permission method at all, does not. The gate
then refuses nothing and records nothing, because a call nobody asked about is
indistinguishable from a call that never happened.

So the promise is enforced one layer down, in the MCP subprocess that owns the
tool: a dry-run seat is spawned with ``--execution-mode dry_run`` and its
``run_code`` and ``manage_routines`` decline their own mutating actions. A tool
that will not run cannot be auto-approved by anybody.

What is asserted here, in the order the fix builds it:

1. the policy, at the layer that now enforces it — the mutating half refused,
   the read-only half (which is all a rehearsal has) untouched;
2. the bypass itself — a bridge that never asks reaches no permission callback,
   and the tool refuses anyway;
3. the wire — the spawner writes the flag only for a mode that restricts, the
   subprocess reads back what the spawner wrote, and the tick engine is what
   tells the builder which kind of run this is;
4. the tool is unharmed — same name, same schema, same description, so the
   model sees no difference until it calls the half it may not have.
"""

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import MCPServer

from condor.acp.client import ACPClient, ToolCallEvent
from condor.agents.risk import RiskState
from condor.runtime.danger import MOUNT_ENFORCED_MODES, mounted_tool_refusal
from condor.runtime.toolsets import _condor_mcp_args
from mcp_servers._profiles import parse_execution_mode
from mcp_servers.condor import server as condor_server

#: Every way into arbitrary Python from the condor seat, and the actions that
#: open it. ``manage_routines``' ``stop`` is here for the reason
#: ``MUTATING_ROUTINE_ACTIONS`` gives: a dry run cannot have started an
#: instance, so the only one it could stop belongs to a live seat.
MUTATING = [
    ("run_code", {"action": "run", "code": "print('executed')"}),
    ("manage_routines", {"action": "run", "name": "lp_scanner"}),
    ("manage_routines", {"action": "run_async", "name": "lp_scanner"}),
    ("manage_routines", {"action": "start", "name": "lp_scanner"}),
    ("manage_routines", {"action": "stop", "name": "lp_scanner"}),
    ("manage_routines", {"action": "create_routine", "name": "x", "code": "pass"}),
    ("manage_routines", {"action": "edit_routine", "name": "x", "code": "pass"}),
    ("manage_routines", {"action": "delete_routine", "name": "x"}),
]

#: What a rehearsal still needs. Refusing these would not make a dry run safer,
#: it would make it impossible: a run that cannot list a routine or read back a
#: past snippet cannot rehearse the tick it exists to rehearse (CORR-625).
READ_ONLY = [
    ("run_code", {"action": "history"}),
    ("run_code", {"action": "get", "run_id": "r1"}),
    ("manage_routines", {"action": "list"}),
    ("manage_routines", {"action": "describe", "name": "lp_scanner"}),
    ("manage_routines", {"action": "read_routine", "name": "lp_scanner"}),
    ("manage_routines", {"action": "list_instances"}),
    ("manage_routines", {"action": "get_instance", "name": "lp_scanner"}),
]


def _ids(value) -> str:
    """``tool:action`` for a parametrize id, so a failure names the call."""
    return str(value.get("action", "")) if isinstance(value, dict) else str(value)


#: What a tool body returns when a call actually arrives at it. The real bodies
#: talk to the main process, which is not running under pytest, so they are
#: stubbed: an *allowed* call must be seen to reach its body, and anything that
#: is neither a refusal nor an arrival — a schema error, a wrapper that crashes
#: before calling through — has to fail the test rather than read as "not
#: refused". Swallowing every exception would let a broken wrapper satisfy both
#: the read-only and the live cases.
_REACHED = "reached the tool body"


def _stub_bodies(monkeypatch, *names: str) -> dict[str, list[dict]]:
    """Replace the named tools' bodies, BEFORE the gate wraps them.

    The gate wraps whatever ``TOOL_PROFILES`` holds, so stubbing has to happen
    in that table: swapping the registered function afterwards would replace the
    wrapper itself and the gate would never run at all.

    ``functools.wraps`` keeps each stub's name, docstring and — through
    ``__wrapped__`` — its signature, so MCPServer derives the same schema it would
    from the real body and the arguments under test still validate.
    """
    import functools

    calls: dict[str, list[dict]] = {name: [] for name in names}

    def _stub_for(real):
        @functools.wraps(real)
        async def stub(**kwargs):
            calls[real.__name__].append(kwargs)
            return {"ok": _REACHED}

        return stub

    patched = {
        profile: tuple(_stub_for(fn) if fn.__name__ in names else fn for fn in tools)
        for profile, tools in condor_server.TOOL_PROFILES.items()
    }
    monkeypatch.setattr(condor_server, "TOOL_PROFILES", patched)
    return calls


def _seat(execution_mode: str, profile: str = "tick") -> MCPServer:
    """The condor subprocess's tool surface for a seat, as it registers it."""
    server = MCPServer("dry-run-probe")
    condor_server.register_tools(server, profile, (), execution_mode)
    return server


def _refusal(server: MCPServer, tool: str, args: dict, calls=None) -> str:
    """The refusal ``tool`` answered with, or ``""`` when the call reached it.

    A refusal is the tool's ordinary ``{"error": ...}`` result, so it comes back
    through ``call_tool`` as content rather than as an exception. With ``calls``
    from :func:`_stub_bodies`, this also asserts the two outcomes are exclusive:
    a refused call must not have run, and an approved one must have arrived.
    """
    result = asyncio.run(server.call_tool(tool, args))
    text = str(result)
    refused = "dry-run mode" in text

    if calls is not None:
        arrived = calls.get(tool, [])
        if refused:
            assert not arrived, f"{tool} was refused and still ran"
        else:
            assert arrived, f"{tool} neither refused nor reached: {text[:200]}"
            assert _REACHED in text

    return text if refused else ""


# ── 1. the policy, at the layer that enforces it ─────────────────────────────


@pytest.mark.parametrize("tool,args", MUTATING, ids=_ids)
def test_a_dry_run_seat_refuses_every_door_onto_python(tool, args, monkeypatch):
    """The CO-2 regression: the snippet does not run, and the model is told why.

    ``calls`` makes "does not run" an assertion rather than an inference — the
    stub body records every arrival, and a refused call must have none.
    """
    calls = _stub_bodies(monkeypatch, "run_code", "manage_routines")

    assert _refusal(_seat("dry_run"), tool, args, calls)


@pytest.mark.parametrize("tool,args", READ_ONLY, ids=_ids)
def test_a_dry_run_seat_still_answers_the_reads_a_rehearsal_needs(
    tool, args, monkeypatch
):
    """Refusing these would make a dry run useless rather than safe.

    Asserted as *reaching the body*, not merely as "no refusal came back": a
    wrapper that crashed on the way would also produce no refusal.
    """
    calls = _stub_bodies(monkeypatch, "run_code", "manage_routines")

    assert not _refusal(_seat("dry_run"), tool, args, calls)


@pytest.mark.parametrize("tool,args", MUTATING, ids=_ids)
def test_a_live_seat_is_untouched(tool, args, monkeypatch):
    """The flag restricts a rehearsal and nothing else.

    A live tick executes routines as ordinary work, which is why neither tool is
    in ``DANGEROUS_TOOLS`` to begin with (SEC-626): gating them by name would
    put a confirmation in front of every tick. Asserted as reaching the body, so
    "the gate is off" cannot be satisfied by the call failing some other way.
    """
    calls = _stub_bodies(monkeypatch, "run_code", "manage_routines")

    assert not _refusal(_seat(""), tool, args, calls)


def test_an_unclassified_action_cannot_be_expressed_on_the_wire():
    """An action the tool does not declare never reaches the gate at all.

    ``run_code``'s ``action`` is a ``Literal``, so MCPServer's schema rejects an
    unknown one before any body or wrapper runs. Pinned because it is *why* the
    test below has to reach past ``call_tool`` to assert fail-closed: there is
    no call that exercises that path from the wire, which is a second lock
    rather than a gap.
    """
    with pytest.raises(Exception):
        asyncio.run(_seat("dry_run").call_tool("run_code", {"action": "teleport"}))


def test_the_gate_fails_closed_on_an_action_it_cannot_classify():
    """An action added to a tool later is refused until it is classified.

    The property ``_is_mutating_action`` already had, asserted at the layer that
    now enforces it. Asked of the policy directly because the schema above stops
    such a call first today — and would stop stopping it the moment someone
    widens the ``Literal`` and forgets ``danger.py``, which is the one case this
    protects. ``action`` omitted is the same question: ``run_code`` defaults to
    ``run``, so "cannot tell" must read as the mutating answer.
    """
    refusal = mounted_tool_refusal("dry_run")
    assert refusal is not None
    assert refusal("run_code", {"action": "teleport"})
    assert refusal("run_code", {"code": "print('executed')"})


# ── 2. the bypass this exists for ────────────────────────────────────────────


def test_a_bridge_that_never_asks_reaches_no_permission_callback():
    """Half of CO-2, on its own: the gate is only as reachable as the bridge.

    The client is driven exactly as a bridge drives it that auto-approves its
    own calls — a ``session/update`` announcing the tool call, and no
    ``session/request_permission`` behind it — and the permission callback,
    which would have refused this very call, is never entered. That is not a
    defect in the callback; it is why a promise about what a session cannot do
    must not rest on one.
    """
    asked: list[dict] = []

    async def permission_callback(tool_call, options):
        asked.append(tool_call)
        return {"outcome": {"outcome": "cancelled"}}

    client = ACPClient(command="true", permission_callback=permission_callback)
    client._current_req_id = 1  # a turn is streaming, so updates are kept

    client._on_session_update(
        sessionId="s1",
        update={
            "sessionUpdate": "tool_call",
            "toolCallId": "tc1",
            "title": "mcp__condor__run_code",
            "rawInput": {"action": "run", "code": "print('executed')"},
            "status": "in_progress",
        },
    )

    # The call reached the client — it is not that nothing happened.
    event = client._event_queue.get_nowait()
    assert isinstance(event, ToolCallEvent)
    assert event.input == {"action": "run", "code": "print('executed')"}

    # ...and the gate that forbids it was never consulted.
    assert asked == [], "no permission request was sent, so nothing was asked"


def test_the_call_that_bridge_let_through_is_refused_by_the_tool():
    """The other half: the same call, at the layer the bridge cannot skip.

    Together with the test above this is the whole fix — the callback was never
    consulted, and the snippet still did not run.
    """
    refusal = _refusal(
        _seat("dry_run"), "run_code", {"action": "run", "code": "print('executed')"}
    )
    assert refusal
    assert "get_market_data" in refusal, "a refusal names the route that does work"


# ── 3. the wire between spawner and subprocess ───────────────────────────────


def test_only_a_mode_that_restricts_reaches_the_spawn_line():
    """A live seat spawns the argv it always did.

    The ``_muted_tool_args`` rule, for the same reason: a flag on every line is
    a flag that can be blamed for every difference between two runs.
    """
    live = _condor_mcp_args(1, 1, profile="tick")
    for mode in ("", "loop", "run_once", "shutdown"):
        assert _condor_mcp_args(1, 1, profile="tick", execution_mode=mode) == live

    dry = _condor_mcp_args(1, 1, profile="tick", execution_mode="dry_run")
    assert dry == live + ["--execution-mode", "dry_run"]


def test_the_subprocess_reads_back_what_the_spawner_wrote(monkeypatch):
    """The wire format end to end, instead of two copies that agree today.

    ``parse_execution_mode`` is what the subprocess calls at import; the argv it
    parses here is the one ``_condor_mcp_args`` actually produced.
    """
    argv = _condor_mcp_args(1, 1, profile="tick", execution_mode="dry_run")
    monkeypatch.setattr("sys.argv", ["mcp_servers.condor", *argv])
    assert parse_execution_mode() == "dry_run"

    monkeypatch.setattr(
        "sys.argv", ["mcp_servers.condor", *_condor_mcp_args(1, 1, profile="tick")]
    )
    assert parse_execution_mode() == ""


def test_the_enforced_modes_are_one_table_both_sides_read():
    """The spawner's "is this worth a flag" and the subprocess's "what does it
    mean" are the same question, so they are not allowed two answers."""
    assert "dry_run" in MOUNT_ENFORCED_MODES
    assert mounted_tool_refusal("dry_run") is not None
    for mode in ("", "loop", "run_once"):
        assert mode not in MOUNT_ENFORCED_MODES
        assert mounted_tool_refusal(mode) is None


@pytest.mark.parametrize("mode", ["dry_run", "loop"])
def test_the_tick_engine_tells_the_builder_which_kind_of_run_this_is(monkeypatch, mode):
    """The one caller that knows the mode, passing it to the *toolset* builder.

    Without this edge every assertion above holds and every dry run still mounts
    an unrestricted seat — which is the shape of the original defect, so the
    edge is pinned rather than left to the next edit of that function.

    Asserted on the captured argument rather than on the source text, because
    ``build_gated_client`` already handed the same ``execution_mode`` to
    ``auto_approve_with_risk_check`` — the permission callback CO-2 walked past.
    A test that grepped for the name would have passed on the unfixed code.
    """
    from condor.agents import engine as engine_mod
    from condor.runtime import llm_client, toolsets

    seen: dict = {}

    def fake_build(*a, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(toolsets, "build_mcp_servers_for_session", fake_build)
    monkeypatch.setattr(llm_client, "build_llm_client", lambda *a, **kw: object())

    engine = SimpleNamespace(
        user_id=1,
        chat_id=2,
        agent_id="a.s_1",
        agent=SimpleNamespace(slug="a", tools=[]),
        risk=SimpleNamespace(),
        ledger=None,
        config={},
        _refusals=None,
        _resolve_server=lambda: ("srv", {}),
        _executor_owners=lambda: {},
        _agent_key=lambda: "claude-code",
    )

    engine_mod.build_gated_client(engine, RiskState(), None, mode)

    assert seen["execution_mode"] == mode


def test_the_builder_takes_the_mode_and_defaults_to_restricting_nothing():
    """Every other caller keeps the seat it had without naming the parameter."""
    from condor.runtime.toolsets import build_mcp_servers_for_session

    param = inspect.signature(build_mcp_servers_for_session).parameters[
        "execution_mode"
    ]
    assert param.default == ""


# ── 4. the tool itself is unharmed ───────────────────────────────────────────


@pytest.mark.parametrize("tool", ["run_code", "manage_routines"])
def test_the_gate_changes_nothing_the_model_can_see(tool):
    """Same name, same schema, same description on a gated seat as on a live one.

    The wrapper takes ``**kwargs``; only ``functools.wraps`` setting
    ``__wrapped__`` keeps ``inspect.signature`` — and so MCPServer's schema — on
    the real parameters. A wrapper that lost them would mount a tool taking no
    arguments, and every dry run would fail for the wrong reason.
    """
    live = {t.name: t for t in asyncio.run(_seat("").list_tools())}[tool]
    dry = {t.name: t for t in asyncio.run(_seat("dry_run").list_tools())}[tool]

    assert dry.name == live.name
    assert dry.description == live.description
    assert dry.inputSchema == live.inputSchema


def test_a_dry_run_mounts_exactly_what_a_live_tick_mounts():
    """The cut is per action, not per tool: nothing is dropped from the ring.

    Muting either tool outright would take its read-only half with it, and the
    tick preload names ``run_code`` as a tool a dry run can still use
    (``test_the_tick_preload_always_carries_a_way_to_read_a_market``).
    """
    live = {t.name for t in asyncio.run(_seat("").list_tools())}
    dry = {t.name for t in asyncio.run(_seat("dry_run").list_tools())}
    assert dry == live
    assert {"run_code", "manage_routines"} <= dry


def test_a_refusal_is_a_result_the_model_can_read_not_a_crash():
    """``{"error": reason}`` — the shape every tool here already says no with.

    An exception would reach the model as a tool failure, which it is apt to
    retry verbatim; a reason is something it can act on.
    """
    result = asyncio.run(
        _seat("dry_run").call_tool("run_code", {"action": "run", "code": "pass"})
    )
    content = result[0] if isinstance(result, tuple) else result
    payload = json.loads(content[0].text)
    assert set(payload) == {"error"}
    assert "dry-run mode" in payload["error"]
