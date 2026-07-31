"""Regression tests for the agent chat's armed text-input modes.

Prompts like "Enter model manually" arm a flag that makes the *next plain
message* mean something other than "talk to the agent". Walking away from such a
prompt by tapping a button used to leave the flag set, so the next real task was
parsed as a model slug ("No tool-calling OpenRouter model matches 'create
trading agent'"). Any callback tap must disarm them before dispatch; handlers
that want an armed mode re-arm their own flag.
"""

import asyncio
from types import SimpleNamespace

import pytest

import utils.auth as auth_module
from handlers.agents import (
    _ARMED_TEXT_INPUT_KEYS,
    _disarm_text_input,
    agent_callback_handler,
)


@pytest.fixture
def approved_user(monkeypatch):
    """Make @restricted see an admin so the handler body actually runs."""
    from config_manager import UserRole

    monkeypatch.setattr(
        auth_module,
        "get_config_manager",
        lambda: SimpleNamespace(get_user_role=lambda _uid: UserRole.ADMIN),
    )


def _callback_update(data):
    """Minimal private-chat callback_query update; records edited text."""
    edits = []

    async def edit_text(text, **kwargs):
        edits.append(text)
        return SimpleNamespace(edit_text=edit_text)

    async def answer(*a, **kw):
        return None

    message = SimpleNamespace(edit_text=edit_text, reply_text=edit_text)
    return (
        SimpleNamespace(
            callback_query=SimpleNamespace(data=data, message=message, answer=answer),
            effective_chat=SimpleNamespace(id=4242, type="private"),
            effective_user=SimpleNamespace(id=7, username="tester"),
            message=None,
        ),
        edits,
    )


def _ctx(**user_data):
    return SimpleNamespace(user_data=dict(user_data), chat_data={})


def _tap(data, **user_data):
    update, edits = _callback_update(data)
    context = _ctx(**user_data)
    asyncio.run(agent_callback_handler(update, context))
    return context, edits


def test_disarm_clears_every_armed_mode():
    ctx = _ctx(**{k: True for k in _ARMED_TEXT_INPUT_KEYS}, agent_llm="claude-code")
    _disarm_text_input(ctx)
    assert not [k for k in _ARMED_TEXT_INPUT_KEYS if k in ctx.user_data]
    # Preferences are not input modes — they must survive.
    assert ctx.user_data["agent_llm"] == "claude-code"


def test_changing_llm_disarms_pending_manual_slug_entry(approved_user):
    """The reported bug: pick another LLM after "Enter model manually"."""
    context, edits = _tap(
        "agent:set_llm:claude-code", _openrouter_typing_slug=True, agent_llm="gemini"
    )

    assert "_openrouter_typing_slug" not in context.user_data
    assert context.user_data["agent_llm"] == "claude-code"
    assert edits and "LLM set to" in edits[0]


def test_openrouter_picker_selection_disarms_manual_slug_entry(approved_user):
    """Same hazard via the picker: the pick lands, the armed flag must not."""
    model = SimpleNamespace(
        slug="anthropic/claude-sonnet-4.5",
        name="Claude Sonnet 4.5",
        prompt_price=0.0,
        completion_price=0.0,
    )
    context, edits = _tap(
        "agent:or_pick:0",
        _openrouter_typing_slug=True,
        _openrouter_models=[model],
    )

    assert "_openrouter_typing_slug" not in context.user_data
    assert context.user_data["agent_llm"] == "openrouter:anthropic/claude-sonnet-4.5"


def test_reopening_the_picker_disarms_manual_slug_entry(approved_user):
    """Backing out of manual entry to the model list also disarms it."""
    context, _ = _tap(
        "agent:or_page:0",
        _openrouter_typing_slug=True,
        _openrouter_models=[],
    )

    assert "_openrouter_typing_slug" not in context.user_data


def test_manual_entry_prompt_still_arms_slug_mode(approved_user):
    """The disarm runs before dispatch, so the prompt that wants it still wins."""
    context, edits = _tap("agent:or_type")

    assert context.user_data["_openrouter_typing_slug"] is True
    assert edits and "slug" in edits[0]


def test_cancelling_manual_entry_disarms_and_drops_the_typed_slug(approved_user):
    context, edits = _tap(
        "agent:or_type_cancel",
        _openrouter_typing_slug=True,
        _openrouter_typed_slug="anthropic/claude-sonnet-4.5",
    )

    assert "_openrouter_typing_slug" not in context.user_data
    assert "_openrouter_typed_slug" not in context.user_data
    assert edits and "Cancelled" in edits[0]


def test_confirming_a_typed_slug_survives_the_disarm(approved_user):
    """Disarm must clear armed flags only — not the pending value confirm needs."""
    context, edits = _tap(
        "agent:or_type_confirm",
        _openrouter_typing_slug=True,
        _openrouter_typed_slug="anthropic/claude-sonnet-4.5",
    )

    assert context.user_data["agent_llm"] == "openrouter:anthropic/claude-sonnet-4.5"
    assert "_openrouter_typing_slug" not in context.user_data


def test_custom_endpoint_add_arms_only_its_own_mode(approved_user):
    """Cross-flow: starting the custom-endpoint flow clears the OpenRouter mode."""
    context, _ = _tap("agent:cu_add", _openrouter_typing_slug=True)

    assert "_openrouter_typing_slug" not in context.user_data
    assert context.user_data["_custom_typing_url"] is True
