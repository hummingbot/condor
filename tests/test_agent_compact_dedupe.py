"""Both compact surfaces run the same routine (ARCH-098).

Compact is reachable two ways: the ``agent:compact_auto`` menu button, which
edits its own callback message, and typed custom instructions, which reply and
then edit that reply. Those used to be two copies of the same fifty lines, so a
fix landed in one and not the other. These tests drive both surfaces through
the same scenarios and assert the rendered text and the runtime calls match --
they fail the moment the two paths diverge again.
"""

import asyncio

import pytest

from handlers import agents as agents_mod


class _FakeSession:
    def __init__(self, alive=True, is_busy=False, agent_key="claude", agent_slug=""):
        self.alive = alive
        self.is_busy = is_busy
        self.agent_key = agent_key
        # Compact recreates the session, so it carries the bound identity over
        # as well as the model (CORR-090).
        self.agent_slug = agent_slug


class _CallbackMessage:
    """The menu surface: every step edits this one message."""

    def __init__(self, rendered: list[str]):
        self._rendered = rendered

    async def edit_text(self, text, **kw):
        self._rendered.append(text)
        return self


class _Placeholder:
    def __init__(self, rendered: list[str]):
        self._rendered = rendered

    async def edit_text(self, text, **kw):
        self._rendered.append(text)
        return self


class _UserMessage:
    """The typed surface: first step replies, later steps edit the reply."""

    def __init__(self, rendered: list[str]):
        self._rendered = rendered

    async def reply_text(self, text, **kw):
        self._rendered.append(text)
        return _Placeholder(self._rendered)


class _Chat:
    id = 4242


class _User:
    id = 99


class _Update:
    def __init__(self, *, callback_message=None, message=None):
        self.callback_query = (
            type("_Q", (), {"message": callback_message})()
            if callback_message
            else None
        )
        self.message = message
        self.effective_chat = _Chat()
        self.effective_user = _User()


class _Context:
    def __init__(self):
        self.bot = object()
        self.user_data: dict = {}


def _patch_runtime(monkeypatch, *, session, summary="a b c", prompt_error=None):
    """Stub the session runtime; return the recorded call log."""
    calls: list[tuple] = []

    async def fake_get_session(chat_id):
        return session

    async def fake_destroy(chat_id):
        calls.append(("destroy", chat_id))
        return True

    async def fake_create(**kw):
        calls.append(("create", kw["chat_id"], kw["agent_key"]))
        return _FakeSession()

    async def fake_prompt_once(key, prompt):
        calls.append(("prompt", prompt))
        first_prompt = sum(1 for c in calls if c[0] == "prompt") == 1
        if prompt_error is not None and first_prompt:
            raise prompt_error
        # Only the summarize prompt answers; the reinjection returns nothing.
        return summary if first_prompt else ""

    monkeypatch.setattr(agents_mod, "get_session", fake_get_session)
    monkeypatch.setattr(agents_mod, "destroy_session", fake_destroy)
    monkeypatch.setattr(agents_mod, "_create_tg_session", fake_create)
    monkeypatch.setattr(
        agents_mod, "_tg_permission_callback", lambda bot, c, u: object()
    )
    monkeypatch.setattr(agents_mod.runtime, "prompt_once", fake_prompt_once)
    return calls


def _run_button(instructions=None):
    rendered: list[str] = []
    update = _Update(callback_message=_CallbackMessage(rendered))
    ctx = _Context()
    asyncio.run(
        agents_mod._handle_compact(update, ctx, custom_instructions=instructions)
    )
    return rendered, ctx


def _run_typed(instructions="keep the SOL setup"):
    rendered: list[str] = []
    update = _Update(message=_UserMessage(rendered))
    ctx = _Context()
    asyncio.run(agents_mod._do_compact_from_message(update, ctx, instructions))
    return rendered, ctx


SCENARIOS = {
    "happy": dict(session=_FakeSession()),
    "no_session": dict(session=None),
    "dead_session": dict(session=_FakeSession(alive=False)),
    "busy": dict(session=_FakeSession(is_busy=True)),
    "empty_summary": dict(session=_FakeSession(), summary="   "),
    "prompt_failed": dict(session=_FakeSession(), prompt_error=RuntimeError("boom")),
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_both_surfaces_render_the_same_text(monkeypatch, scenario):
    """Same instructions, same scenario -> byte-identical rendered sequence."""
    kw = SCENARIOS[scenario]

    button_calls = _patch_runtime(monkeypatch, **kw)
    button_rendered, _ = _run_button(instructions="keep the SOL setup")

    typed_calls = _patch_runtime(monkeypatch, **kw)
    typed_rendered, _ = _run_typed("keep the SOL setup")

    assert button_rendered == typed_rendered
    assert button_calls == typed_calls


def test_compact_carries_the_summary_over_on_both_surfaces():
    """The word-count confirmation is the contract both surfaces must keep."""
    for runner in (lambda: _run_button("keep the SOL setup"), _run_typed):
        with pytest.MonkeyPatch.context() as mp:
            _patch_runtime(mp, session=_FakeSession(), summary="one two three four")
            rendered, _ = runner()

        assert rendered[0] == "Compacting context..."
        assert rendered[-1].startswith("Context compacted (4 words carried over).")


def test_compact_sequence_is_summarize_destroy_recreate_reinject(monkeypatch):
    """Order matters: the summary must be taken before the session is destroyed."""
    calls = _patch_runtime(monkeypatch, session=_FakeSession(agent_key="claude"))
    _run_button()

    kinds = [c[0] for c in calls]
    assert kinds == ["prompt", "destroy", "create", "prompt"]
    assert calls[2] == ("create", 4242, "claude")


def test_auto_and_custom_use_their_own_prompt_templates(monkeypatch):
    """The only thing the button path varies is the template it sends."""
    auto_calls = _patch_runtime(monkeypatch, session=_FakeSession())
    _run_button(instructions=None)
    assert auto_calls[0][1] == agents_mod.COMPACT_PROMPT_AUTO

    custom_calls = _patch_runtime(monkeypatch, session=_FakeSession())
    _run_button(instructions="keep the SOL setup")
    assert "keep the SOL setup" in custom_calls[0][1]
    assert custom_calls[0][1] != agents_mod.COMPACT_PROMPT_AUTO


def test_busy_typed_path_stays_armed_for_a_retry(monkeypatch):
    """A busy agent must not swallow the typed-instructions mode."""
    _patch_runtime(monkeypatch, session=_FakeSession(is_busy=True))
    _, typed_ctx = _run_typed()
    assert typed_ctx.user_data.get("agent_compact_custom") is True

    _patch_runtime(monkeypatch, session=_FakeSession(is_busy=True))
    _, button_ctx = _run_button("keep the SOL setup")
    assert "agent_compact_custom" not in button_ctx.user_data
