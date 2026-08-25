"""What Telegram does about a pasted key (FEAT-056).

The safety property is not tested here — it lives on the funnel and is pinned
in ``tests/runtime/test_secrets_funnel.py``. What this file pins is the half
only this surface can do: deleting the message that carried the phrase, saying
why the text on screen just changed, and warning about the ambiguous shapes
rarely enough that the warning is still read.
"""

import asyncio
from types import SimpleNamespace

import pytest

from handlers.agents import _SECRET_SEEN_KEY, _handle_pasted_secrets

SEED = "legal winner thank year wave sausage worth useful legal winner thank yellow"
TX_HASH = "0x" + "9f" * 32


def _update():
    """A private-chat message that records whether it was deleted."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=4242, type="private"),
        effective_user=SimpleNamespace(id=7),
        deleted=[],
    )

    async def delete():
        update.deleted.append(True)

    update.message = SimpleNamespace(delete=delete)
    return update


def _context(**user_data):
    """A bot that records what it sent, plus empty user/chat data."""
    sent: list = []

    async def send_message(chat_id, text, **kwargs):
        sent.append(SimpleNamespace(text=text, markup=kwargs.get("reply_markup")))

    return SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
        user_data=dict(user_data),
        chat_data={},
        sent=sent,
    )


def _run(text, context=None, update=None):
    update = update or _update()
    context = context or _context()
    asyncio.run(_handle_pasted_secrets(update, context, text))
    return SimpleNamespace(
        deleted=update.deleted, sent=context.sent, context=context, update=update
    )


def test_a_message_carrying_a_phrase_is_deleted_and_explained():
    out = _run(f"import this for me: {SEED}")
    assert out.deleted == [True]
    assert len(out.sent) == 1
    assert "/gateway" in out.sent[0].text
    assert "sausage" not in out.sent[0].text


def test_a_delete_that_fails_still_explains():
    """Telegram keeps the message until the delete lands, and it is best
    effort — the same bare-except the wallet flow has carried for years."""
    update = _update()

    async def boom():
        raise RuntimeError("message can't be deleted")

    update.message.delete = boom
    out = _run(SEED, update=update)
    assert len(out.sent) == 1
    assert "/gateway" in out.sent[0].text


def test_ordinary_text_says_nothing_and_deletes_nothing():
    for text in ("how is SOL-USDC doing?", "restart hummingbot-pmm-1", ""):
        out = _run(text)
        assert out.deleted == []
        assert out.sent == []


def test_a_transaction_hash_is_warned_about_once_per_conversation():
    context = _context()
    sent = context.sent
    _run(f"did {TX_HASH} land?", context=context)
    assert len(sent) == 1
    assert "transaction hash" in sent[0].text
    assert sent[0].markup is not None  # the "stop warning me" button

    _run(f"and {TX_HASH} again?", context=context)
    assert len(sent) == 1, "a warning that fires every time is one nobody reads"


def test_each_kind_gets_its_own_warning():
    """Being warned about a hash must not use up the warning about a key."""
    sol_sig = (
        "5wHu1qwD4kLwYqLNGjaKfHUDNCLLDFFPGz1cUKb1t8HBxXpJhVFq"
        "1PbwzTV1RxRuFuvLWqJwHtDsL1s9jUn9Xg1H"
    )
    context = _context()
    sent = context.sent
    _run(f"check {TX_HASH}", context=context)
    _run(f"check {sol_sig}", context=context)
    assert len(sent) == 2
    assert "base58" in sent[1].text


def test_a_new_conversation_is_warned_again():
    context = _context()
    sent = context.sent
    _run(f"did {TX_HASH} land?", context=context)
    # A reset leaves the chat on another conversation id.
    from condor.preferences import set_chat_binding

    set_chat_binding(context.user_data, {"conversation_id": "conv-2"})
    _run(f"did {TX_HASH} land?", context=context)
    assert len(sent) == 2


def test_the_notice_obeys_the_silence_preference():
    context = _context()
    sent = context.sent
    from condor.preferences import set_secret_notices

    set_secret_notices(context.user_data, False)
    _run(f"did {TX_HASH} land?", context=context)
    assert sent == []


def test_silencing_the_notice_does_not_silence_the_removal():
    """Nothing about the certain shapes is optional. The message that explains
    a deletion is not a notice — it is why the user's text just vanished."""
    context = _context()
    sent = context.sent
    from condor.preferences import set_secret_notices

    set_secret_notices(context.user_data, False)
    out = _run(SEED, context=context)
    assert out.deleted == [True]
    assert len(sent) == 1


def test_the_seen_flag_is_a_kind_and_never_a_value():
    context = _context()
    _run(f"did {TX_HASH} land?", context=context)
    flags = context.chat_data[_SECRET_SEEN_KEY]
    assert flags == {"": ["evm-hex64"]}
    assert TX_HASH not in repr(flags)


@pytest.mark.parametrize("start_on", [True, False])
def test_the_settings_toggle_flips_the_preference(start_on):
    from condor.preferences import secret_notices_enabled, set_secret_notices

    user_data: dict = {}
    set_secret_notices(user_data, start_on)
    assert secret_notices_enabled(user_data) is start_on
    set_secret_notices(user_data, not start_on)
    assert secret_notices_enabled(user_data) is (not start_on)
