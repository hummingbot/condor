"""A config change reaches the chat that is already open (FEAT-093).

A chat session is the only runtime that holds one client across turns: the tick
engine builds a fresh client every tick and ``consult``/``delegate`` are
one-shot. ``ACPClient`` sends ``mcpServers`` and the system prompt exactly once,
inside ``session/new``, and MCP tools register at subprocess import off argv —
so a mute switched on in the brain panel used to reach the *next* chat and never
this one.

The fix is not an invalidation anybody publishes. Staleness is recomputed:
:meth:`SessionBinding.fingerprint` digests the resolved configuration, the
session remembers its digest, and ``client.prompt`` compares them at the start
of each turn. Writers need no discipline, the MCP subprocess's writes are seen
for free (the filesystem is the channel), N changes coalesce into one reload and
a change that nets to nothing costs nothing.

The load-bearing tests are the two that guard the failure mode. A digest that
moved on its own would respawn every chat on *every* message and silently
truncate each one to the replay budget, so ``test_the_digest_is_the_same_twice``
and ``test_ten_turns_with_no_change_spawn_exactly_one_client`` are the reason
the rest of this is safe to put on the path every turn takes.
"""

import asyncio
import inspect

import pytest

import routines.base as routines_base
from condor.acp.client import PromptDone, TextChunk
from condor.agents import agent as agent_module
from condor.memory.mutes import set_muted
from condor.runtime import PromptRequest, SessionKey, SessionSpec, binding
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime import sessions as session_module
from condor.runtime.events import EventType

KEY = SessionKey.telegram(42)


class _FakeClient:
    """Counts spawns and remembers the argv it was configured with."""

    last: "_FakeClient | None" = None
    spawns = 0

    def __init__(self, **kwargs):
        self.alive = True
        self.kwargs = kwargs
        self.prompts: list[str] = []
        self._release: asyncio.Event | None = None
        type(self).last = self
        type(self).spawns += 1

    @property
    def mcp_argv(self) -> list[str]:
        """Flat argv of every MCP server this client was built with."""
        return [
            arg
            for server in (self.kwargs.get("mcp_servers") or [])
            for arg in server.get("args", [])
        ]

    async def start(self):
        pass

    async def stop(self):
        self.alive = False
        if self._release:
            self._release.set()

    async def prompt(self, text):
        self.prompts.append(text)
        return "ok"

    async def abort_prompt(self):
        if self._release:
            self._release.set()

    def gate(self) -> asyncio.Event:
        """Hold the next turn open until the returned event is set."""
        self._release = asyncio.Event()
        return self._release

    async def prompt_stream(self, text):
        self.prompts.append(text)
        yield TextChunk(text=f"answering {text}")
        if self._release is not None:
            await self._release.wait()
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """An empty registry over a throwaway ``agents/`` tree.

    ``build_mcp_servers_for_session`` is deliberately **not** stubbed: the whole
    question this feature answers is whether a mute written to disk changes what
    the next spawn's argv carries, and a stub would answer it about the stub.
    """
    monkeypatch.setattr(agent_module, "_DATA_ROOT", tmp_path)
    # ``routines.base`` resolves the repo root at import, so without this the
    # routines half of the mute assertions would write into the real library.
    monkeypatch.setattr(routines_base, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr("condor.acp.client.ACPClient", _FakeClient)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    _FakeClient.last = None
    _FakeClient.spawns = 0
    return session_module


def _spec(**kwargs) -> SessionSpec:
    base = dict(key=str(KEY), agent_key="claude-code", chat_id=42, user_id=42)
    base.update(kwargs)
    return SessionSpec(**base)


async def _turn(text: str = "hello", **kwargs) -> list:
    return [e async for e in runtime.prompt(KEY, PromptRequest(text=text), **kwargs)]


def _types(events) -> list:
    return [e.type for e in events]


async def _until_busy() -> None:
    """Wait for the session to actually hold its prompt lock."""
    for _ in range(400):
        session = session_module.get_session(KEY)
        if session and session.is_busy:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("session never became busy")


# ── The fingerprint ──


def test_the_digest_is_the_same_twice(registry):
    """Two resolves of an unchanged configuration agree, part for part.

    The one that matters. A digest that drifts on its own — an unsorted set, a
    dict iteration order, a timestamp — would make every chat session look stale
    on every message, respawn it, and throw away everything older than the 6000
    character replay budget. Silently, on every surface, for every user.
    """
    spec = _spec()

    first = binding.resolve(spec).fingerprint()
    second = binding.resolve(spec).fingerprint()

    assert first == second
    assert set(first) == set(binding.CONFIG_PARTS)
    assert all(len(digest) == 16 for digest in first.values())


def test_each_kind_of_change_moves_its_own_part(registry, tmp_path):
    """One part per kind of change — so a reload can say *what* it applied."""
    base = binding.resolve(_spec()).fingerprint()

    # The model: a deliberate pick on the spec overrides the Agent's default.
    assert binding.resolve(_spec(agent_key="gemini")).fingerprint()["model"] != (
        base["model"]
    )

    # A tool muted for this agent rides down as ``--mute-tools`` on the MCP
    # subprocess's argv, which is what ``tools`` digests.
    set_muted("condor", "tool", "manage_clmm", True)
    muted = binding.resolve(_spec()).fingerprint()
    assert muted["tools"] != base["tools"]
    assert muted["libraries"] == base["libraries"]
    set_muted("condor", "tool", "manage_clmm", False)

    # A playbook or routine muted for it is read straight off ``mutes.yml``:
    # neither is baked into argv, so ``libraries`` is the part that carries them.
    set_muted("condor", "skill", "lp_rebalance", True)
    libraries = binding.resolve(_spec()).fingerprint()
    assert libraries["libraries"] != base["libraries"]
    assert libraries["tools"] == base["tools"]
    set_muted("condor", "skill", "lp_rebalance", False)

    # Identity and server come off the Agent record itself, so they are asserted
    # on a bound specialist — the one shape that has one to edit.
    store = agent_module.AgentStore()
    store.create(
        name="Perps",
        description="Trades perps",
        instructions="You trade perps.",
        agent_key="claude-code",
        server_required=False,
    )
    bound_base = binding.resolve(_spec(agent_slug="perps")).fingerprint()

    agent = store.get("perps")
    agent.instructions = "You are something else now."
    agent.server_name = "somewhere-else"
    agent.server_required = True
    store.update(agent)
    moved = binding.resolve(_spec(agent_slug="perps")).fingerprint()

    assert moved["identity"] != bound_base["identity"]
    assert moved["server"] != bound_base["server"]


def test_a_mute_toggled_off_and_back_on_is_the_original_digest(registry):
    """Self-cancelling: content hashed, not writes counted.

    Switching a playbook off and on again before the next message is the digest
    the session already has, so it costs no reload. A dirty flag or a generation
    counter cannot express that; this gets it for free.
    """
    before = binding.resolve(_spec()).fingerprint()

    set_muted("condor", "routine", "lp_scanner", True)
    assert binding.resolve(_spec()).fingerprint() != before
    set_muted("condor", "routine", "lp_scanner", False)

    assert binding.resolve(_spec()).fingerprint() == before


# ── The turn ──


def test_ten_turns_with_no_change_spawn_exactly_one_client(registry):
    """The false-positive regression test, and an acceptance criterion.

    A chat nobody reconfigured must never reload: the comparison now runs on the
    path every turn on every surface takes, and its failure mode is not a crash
    but a chat quietly losing its scrollback ten times over.
    """

    async def scenario():
        await runtime.create_session(_spec())
        for i in range(10):
            events = await _turn(f"message {i}")
            assert EventType.RELOADED not in _types(events)

    asyncio.run(scenario())

    assert _FakeClient.spawns == 1


def test_muting_a_tool_mid_chat_reaches_this_chat(registry):
    """The feature, end to end.

    Mute a tool with the chat open, send a message: the session behind the same
    key is a new subprocess whose argv no longer offers the tool, it has read
    the conversation so far, and the message that triggered the reload is the
    one it answers.
    """

    async def scenario():
        await runtime.create_session(_spec())
        before = session_module.get_session(KEY)
        assert "--mute-tools" not in _FakeClient.last.mcp_argv
        await _turn("how deep is the book?")
        conversations.flush_all()

        set_muted("condor", "tool", "manage_clmm", True)
        events = await _turn("what is the pool at?")

        after = session_module.get_session(KEY)
        return before, after, events

    before, after, events = asyncio.run(scenario())

    assert _FakeClient.spawns == 2
    assert after is not before
    # Same seat, same transcript: a reload must not change its own address.
    assert after.key == before.key
    assert after.conversation_id == before.conversation_id
    # The new subprocess is told to leave the tool unregistered.
    argv = _FakeClient.last.mcp_argv
    assert "--mute-tools" in argv
    assert argv[argv.index("--mute-tools") + 1] == "manage_clmm"
    # It opened on the conversation it inherited — the replay *is* the resume
    # mechanism, so a reload that skipped it would be a new chat under an old
    # key — and then answered the message that caused the reload.
    assert "how deep is the book?" in _FakeClient.last.prompts[0]
    assert _FakeClient.last.prompts[-1].endswith("what is the pool at?")
    assert EventType.RELOADED in _types(events)
    assert EventType.DONE in _types(events)


def test_the_reload_names_the_part_it_applied(registry):
    """The event carries part *names* — never a digest, never a value.

    The inputs behind ``tools`` include the MCP servers' env, API keys and all,
    so the name is the most that may ever cross the boundary.
    """

    async def scenario():
        await runtime.create_session(_spec())
        set_muted("condor", "skill", "lp_rebalance", True)
        return await _turn()

    events = asyncio.run(scenario())

    reloaded = next(e for e in events if e.type is EventType.RELOADED)
    assert reloaded.field("parts") == ["libraries"]


def test_six_changes_before_the_next_message_cost_one_reload(registry):
    """N changes coalesce: there is no queue of pending changes, only a digest."""

    async def scenario():
        await runtime.create_session(_spec())
        for name in ("a", "b", "c", "d", "e", "f"):
            set_muted("condor", "skill", name, True)
        return await _turn()

    events = asyncio.run(scenario())

    assert _FakeClient.spawns == 2
    assert len([e for e in events if e.type is EventType.RELOADED]) == 1


def test_a_change_reverted_before_the_next_message_costs_nothing(registry):
    """Switch it off, switch it back on, send: no reload, no context lost."""

    async def scenario():
        await runtime.create_session(_spec())
        set_muted("condor", "tool", "manage_clmm", True)
        set_muted("condor", "tool", "manage_clmm", False)
        return await _turn()

    events = asyncio.run(scenario())

    assert _FakeClient.spawns == 1
    assert EventType.RELOADED not in _types(events)


def test_a_stale_session_that_is_answering_is_not_replaced(registry):
    """A background change must not SIGTERM the turn in flight.

    Deferred, never queued: nothing remembers that a reload is owed, because the
    digest is still stale at the next turn and the next turn does it. So the
    message sent mid-answer is answered by the old session, and the one after it
    is not.
    """

    async def scenario():
        await runtime.create_session(_spec())
        gate = _FakeClient.last.gate()

        first = asyncio.create_task(_turn("the long one"))
        await _until_busy()

        set_muted("condor", "tool", "manage_clmm", True)
        # Queued behind the turn in flight: the refresh runs before it blocks
        # on the session lock, sees a busy session, and skips.
        second = asyncio.create_task(_turn("sent mid-answer", on_busy="queue"))
        await asyncio.sleep(0.05)
        spawns_during = _FakeClient.spawns

        gate.set()
        await first
        queued = await second
        after = await _turn("the next one")
        return queued, spawns_during, after

    queued, spawns_during, after = asyncio.run(scenario())

    # The turn in flight was left alone, and so was the one queued behind it.
    assert spawns_during == 1
    assert EventType.RELOADED not in _types(queued)
    # The turn after it pays the reload instead.
    assert _FakeClient.spawns == 2
    assert EventType.RELOADED in _types(after)


def test_the_reload_is_recorded_in_the_transcript(registry):
    """The reader is told why the counterpart suddenly has less of the chat."""

    async def scenario():
        await runtime.create_session(_spec())
        conversation_id = session_module.get_session(KEY).conversation_id
        set_muted("condor", "tool", "manage_clmm", True)
        await _turn()
        conversations.flush_all()
        return conversation_id

    conversation_id = asyncio.run(scenario())

    turns = conversations.read_transcript(42, conversation_id)
    notes = [t for t in turns if t.role == "system" and t.kind == "reload"]
    assert len(notes) == 1
    assert notes[0].text == "Reloaded to apply configuration changes (tools)"
    # In front of the turn it explains, and only ever written once the respawn
    # has actually happened — a reload that failed leaves no note claiming it did.
    assert turns[0] is notes[0]
    assert any(turn.role != "system" for turn in turns[1:])


def test_a_deliberate_model_switch_still_replaces_a_busy_session(registry):
    """Only the *background* change defers; an explicit one keeps its behaviour.

    A user switching model mid-turn asked for it, and the frontend already
    disables the picker while busy. The deferral is scoped to sessions whose
    only difference from the spec is a configuration that moved underneath them.
    """

    async def scenario():
        await runtime.create_session(_spec())
        _FakeClient.last.gate()
        turn = asyncio.create_task(_turn("the long one"))
        await _until_busy()

        # Mid-turn, and replaced anyway: the teardown releases the held turn.
        await runtime.create_session(_spec(agent_key="gemini"))
        spawns = _FakeClient.spawns
        await turn
        return spawns

    assert asyncio.run(scenario()) == 2


def test_digests_never_reach_the_logs(registry, caplog):
    """Part names are loggable; the digests and their inputs are not."""

    async def scenario():
        await runtime.create_session(_spec())
        set_muted("condor", "tool", "manage_clmm", True)
        fingerprint = binding.resolve(_spec()).fingerprint()
        with caplog.at_level("DEBUG"):
            await _turn()
        return fingerprint

    fingerprint = asyncio.run(scenario())

    logged = caplog.text
    assert "tools" in logged  # the part name is the point of naming them
    for digest in fingerprint.values():
        assert digest not in logged


def test_a_running_delegation_keeps_the_seat_it_started_with(registry):
    """A config change cannot reach a delegation, by construction.

    A delegation is one-shot: it builds its own client and stops it when the
    task ends, and there is no boundary inside it at which a swap would be
    anything but destroying the work. It is out of scope on purpose — and it is
    out of *reach* because it never enters the session registry, which is the
    only thing ``refresh_if_stale`` can act on.
    """
    from condor.agents import consult

    source = inspect.getsource(consult)
    assert "runtime.prompt" not in source
    assert "get_or_create_session" not in source

    refresh_src = inspect.getsource(session_module.refresh_if_stale)
    assert "_sessions.get(" in refresh_src


# ── The loop side ──


def test_the_tick_reads_its_routines_fresh(registry, tmp_path):
    """A routine muted between ticks leaves the next tick's prompt.

    The loop already re-reads everything else every tick — a fresh client, a
    fresh toolset, a fresh skills index — but the routines section was built
    once per loop on the comment "routines rarely change mid-session", which
    FEAT-090 made false: a loop running for a week would never notice a mute.

    Asserted at the two points the bug lived at: the engine keeps no
    cross-tick cache of the section, and the section it builds honours the mute.
    """
    from condor.agents.engine import TickEngine
    from condor.agents.prompts import _build_routines_section

    assert "_cached_routines_section" not in TickEngine.__dataclass_fields__
    tick_src = inspect.getsource(TickEngine._tick)
    assert "_build_routines_section(self.strategy)" in tick_src

    routines_dir = routines_base.assistant_routines_dir("perps")
    routines_dir.mkdir(parents=True, exist_ok=True)
    (routines_dir / "lp_scanner.py").write_text(
        "from pydantic import BaseModel\n\n\n"
        "class Config(BaseModel):\n"
        '    """Scan the pools."""\n\n\n'
        "async def run(config, context):\n"
        '    return "ok"\n'
    )

    class _Strategy:
        agent_slug = "perps"

    assert "lp_scanner" in _build_routines_section(_Strategy())
    set_muted("perps", "routine", "lp_scanner", True)
    assert "lp_scanner" not in _build_routines_section(_Strategy())
