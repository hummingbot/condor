"""Tests for the /memory handler view (handlers.memory).

Focus: CORR-042 — delete callback_data must never exceed Telegram's hard 64-byte
limit, no matter how long an assistant slug or a memory name is.
"""

from types import SimpleNamespace

import pytest

from condor.memory import paths as paths_module
from condor.memory.store import MemoryStore
from handlers.memory import _DEL_MAP_KEY, _build_view

# Telegram's hard cap on callback_data.
_CALLBACK_DATA_MAX = 64


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """Point the project root at a tmp dir so stores resolve under it."""
    monkeypatch.setattr(paths_module, "_PROJECT_ROOT", tmp_path)
    return tmp_path


def _fake_context():
    """Minimal stand-in for ContextTypes.DEFAULT_TYPE: just needs user_data."""
    return SimpleNamespace(user_data={})


def _all_buttons(keyboard):
    for row in keyboard.inline_keyboard:
        for btn in row:
            yield btn


def test_delete_callback_data_within_64_bytes_for_long_slugs(memory_root):
    user_id = 99
    # Lengths chosen so the OLD scheme (memory:del:{slug}:{name}) would exceed
    # Telegram's 64-byte cap, while staying writable under the OS filename limit.
    long_agent_slug = "a" * 40  # uncapped agent slug
    long_name = "Remember " + "b" * 90  # -> uncapped long memory slug

    store = MemoryStore(user_id, agent_slug=long_agent_slug)
    res = store.write(
        name=long_name,
        content="some content",
        description="a long memory under a long agent slug",
        type="fact",
        source="chat",
    )
    assert res["saved"] is True

    context = _fake_context()
    _text, keyboard = _build_view(user_id, context)

    del_buttons = [
        b for b in _all_buttons(keyboard) if b.callback_data.startswith("memory:del:")
    ]
    assert del_buttons, "expected at least one delete button"
    for btn in del_buttons:
        assert (
            len(btn.callback_data.encode("utf-8")) <= _CALLBACK_DATA_MAX
        ), f"callback_data too long: {btn.callback_data!r}"

    # The index must resolve back to the real (agent_slug, name) pair.
    del_map = context.user_data[_DEL_MAP_KEY]
    idx = int(del_buttons[0].callback_data.rsplit(":", 1)[1])
    agent_slug, name = del_map[idx]
    assert agent_slug == long_agent_slug
    assert MemoryStore(user_id, agent_slug).delete(name, source="user") is True


def test_del_map_indexes_match_buttons_across_stores(memory_root):
    user_id = 123
    MemoryStore(user_id, agent_slug=None).write(
        "Chat fact", "c", "chat fact", type="fact"
    )
    MemoryStore(user_id, agent_slug="alpha").write(
        "Alpha fact", "a", "alpha fact", type="fact"
    )

    context = _fake_context()
    _text, keyboard = _build_view(user_id, context)
    del_map = context.user_data[_DEL_MAP_KEY]

    del_buttons = [
        b for b in _all_buttons(keyboard) if b.callback_data.startswith("memory:del:")
    ]
    # Every delete button's index is present in the map, and every mapped entry
    # resolves to an actually-deletable memory.
    for btn in del_buttons:
        idx = int(btn.callback_data.rsplit(":", 1)[1])
        assert idx in del_map
        agent_slug, name = del_map[idx]
        assert MemoryStore(user_id, agent_slug).read(name) is not None
