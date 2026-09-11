"""What the dashboard says about a pasted key (FEAT-056).

The web composer cannot delete what the user typed the way Telegram can, so
this surface only reports: one ``secret_notice`` per kind, and — because a web
slot id *is* its conversation id — at most once per conversation, which is the
same bound the Telegram side keeps in ``chat_data``.

The redaction itself happens on the funnel and is pinned in
``tests/runtime/test_secrets_funnel.py``; here it only has to be visible in the
text the fake client was handed.
"""

import asyncio
import json
from pathlib import Path

import pytest

from condor.acp.client import PromptDone, TextChunk
from condor.runtime import conversations, secrets
from condor.runtime import sessions as session_module
from condor.web.routes import chat_ws
from condor.web.routes.chat_ws import _handle_send_message, _handle_start_session

USER = 909
SEED = "legal winner thank year wave sausage worth useful legal winner thank yellow"
TX_HASH = "0x" + "9f" * 32


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def events(self, name: str) -> list[dict]:
        return [e for e in self.sent if e.get("event") == name]


class _Client:
    def __init__(self, **kwargs):
        self.alive = True
        self.prompts: list[str] = []
        type(self).last = self

    async def start(self):
        pass

    async def stop(self):
        self.alive = False

    async def prompt(self, text):
        return "ok"

    async def prompt_stream(self, text):
        self.prompts.append(text)
        yield TextChunk(text="ok")
        yield PromptDone(stop_reason="end_turn")


@pytest.fixture
def ws_env(monkeypatch):
    monkeypatch.setattr(conversations, "_live_recorders", set())
    monkeypatch.setattr(session_module, "_sessions", {})
    monkeypatch.setattr("condor.acp.client.ACPClient", _Client)
    monkeypatch.setattr(session_module, "build_initial_context", lambda *a, **k: "")
    # This file asserts the opening turn verbatim, so the conversation's
    # attribution block is blanked alongside the context builder above — it
    # is covered on its own in tests/runtime/test_conversation_attribution.py
    monkeypatch.setattr(session_module, "conversation_attribution", lambda *a, **k: "")
    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", lambda *a, **k: []
    )
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a, **k: {})
    monkeypatch.setattr(chat_ws, "_active_prompt_tasks", {})
    monkeypatch.setattr(chat_ws, "_slot_gates", {})
    monkeypatch.setattr(chat_ws, "_secret_notices_sent", {})
    return chat_ws


def _run(*texts):
    async def scenario():
        ws = _FakeWS()
        await _handle_start_session(ws, USER, {"agent_key": "claude-code"})
        slot_id = ws.events("session_started")[0]["slot_id"]
        for text in texts:
            await _handle_send_message(ws, USER, {"slot_id": slot_id, "text": text})
        return ws

    return asyncio.run(scenario())


def test_a_phrase_is_reported_as_certain_and_never_reaches_the_model(ws_env):
    ws = _run(f"import my {SEED}")
    (notice,) = ws.events("secret_notice")
    assert notice["kind"] == "mnemonic"
    assert notice["certain"] is True
    assert "sausage" not in json.dumps(notice)
    assert _Client.last.prompts == ["import my [redacted: mnemonic]"]


def test_an_ambiguous_shape_is_reported_once_per_conversation(ws_env):
    ws = _run(f"did {TX_HASH} land?", f"and {TX_HASH} again?")
    notices = ws.events("secret_notice")
    assert len(notices) == 1
    assert notices[0] == {
        "event": "secret_notice",
        "slot_id": notices[0]["slot_id"],
        "kind": "evm-hex64",
        "certain": False,
    }
    # Passed through untouched, which is the whole point of "ambiguous".
    assert all(TX_HASH in prompt for prompt in _Client.last.prompts)


def test_ordinary_text_says_nothing(ws_env):
    ws = _run("how is SOL-USDC doing?")
    assert ws.events("secret_notice") == []


def test_the_ambiguous_notice_obeys_the_silence_preference(ws_env, monkeypatch):
    from condor.preferences import set_secret_notices

    silenced: dict = {}
    set_secret_notices(silenced, False)
    monkeypatch.setattr("condor.preferences.load_user_data_for", lambda *a: silenced)

    ws = _run(f"did {TX_HASH} land?", f"import my {SEED}")
    kinds = [notice["kind"] for notice in ws.events("secret_notice")]
    assert kinds == ["mnemonic"], "a removal is not a notice, and is not optional"


def test_every_kind_the_backend_can_send_has_dashboard_copy():
    """The event carries a kind, never the wording — so a kind renamed on one
    side and not the other fails open: no notice, no error, nothing on screen.
    Cheaper to assert than to notice."""
    source = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "src"
        / "hooks"
        / "useChatSocket.ts"
    ).read_text(encoding="utf-8")
    table = source.split("const SECRET_NOTICES")[1].split("};")[0]
    for kind in secrets.KINDS:
        assert kind in table, kind
