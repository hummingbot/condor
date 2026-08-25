"""Deleting a Gateway token from Telegram is owner-only (SEC-225).

SEC-207 drew this line on the web side: registering a token is a precondition of
trading a pool and stays at TRADER, but *removing* an entry from a network's token
list is a mutation of the owner's configuration. Gateway skips token accounts whose
mint is not on the list, so a deleted entry makes that mint's balance read 0 for
every user of the server and every bot sizing an order off it — not just for the
person who pressed the button.

The Telegram side of the same list had no permission check at all: `/gateway →
Tokens` gated only on `@restricted` (an approved Condor user), so any shared trader
with the server selected reached the owner's list. Two paths did the damage — the
explicit delete, and the edit flow, which is a delete-then-re-add underneath.

Both are pinned here, along with the reads and the additive path that deliberately
stay where they were.
"""

import asyncio
from types import SimpleNamespace

import pytest

import handlers.config.gateway.tokens as tokens
from config_manager import ServerPermission

SERVER = "alpha"
CHAT_ID = 4242
OWNER_ID = 1
TRADER_ID = 2
NETWORK = "solana-mainnet-beta"
MINT = "9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump"


class FakeGateway:
    def __init__(self):
        self.deletes = []
        self.adds = []

    async def delete_token(self, network_id, token_address):
        self.deletes.append((network_id, token_address))
        return {"ok": True}

    async def add_token(self, network_id, address, symbol, decimals, name=None):
        self.adds.append((network_id, address, symbol, decimals))
        return {"ok": True}


class FakeConfigManager:
    """Only the surface the token handlers actually touch."""

    def __init__(self, permission, gateway):
        self._permission = permission
        self._gateway = gateway
        self.dialed = []

    def get_server(self, name):
        return {"host": "localhost"} if name == SERVER else None

    def get_chat_default_server(self, chat_id):
        return SERVER

    def get_server_permission(self, user_id, server_name):
        return self._permission

    def is_admin(self, user_id):
        return False

    async def get_client_for_chat(self, chat_id, user_id=None, preferred_server=None):
        self.dialed.append(preferred_server)
        return SimpleNamespace(gateway=self._gateway)


@pytest.fixture
def gateway_as(monkeypatch):
    """Install a config manager answering `permission`, and return its Gateway."""

    def _install(permission):
        gw = FakeGateway()
        cm = FakeConfigManager(permission, gw)
        gw.cm = cm
        import config_manager

        monkeypatch.setattr(config_manager, "get_config_manager", lambda: cm)
        # The success paths refresh the token list after a two-second pause;
        # neither is what these tests are about.
        monkeypatch.setattr(asyncio, "sleep", _noop)
        monkeypatch.setattr(tokens, "show_network_tokens", _noop)
        return gw

    return _install


async def _noop(*args, **kwargs):
    return None


class FakeQuery:
    """A callback query, with what it answered and rendered kept for inspection."""

    def __init__(self, user_id):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []
        self.texts = []
        self.message = SimpleNamespace(
            chat_id=CHAT_ID, message_id=7, edit_text=self._edit_text
        )

    async def answer(self, text="", show_alert=False):
        self.answers.append((text, show_alert))

    async def _edit_text(self, text, parse_mode=None, reply_markup=None):
        self.texts.append(text)


class FakeBot:
    def __init__(self):
        self.texts = []

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None):
        self.texts.append(text)

    async def send_message(self, chat_id, text, parse_mode=None):
        self.texts.append(text)


class FakeUpdate:
    """A text message arriving mid-flow, as the edit branch sees it."""

    def __init__(self, user_id, text):
        self.bot = FakeBot()
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=CHAT_ID)
        self.message = SimpleNamespace(text=text, delete=_noop)

    def get_bot(self):
        return self.bot


def _context():
    return SimpleNamespace(user_data={})


def _edit_context():
    return SimpleNamespace(
        user_data={
            "awaiting_token_input": "token_edit",
            "token_network": NETWORK,
            "token_message_id": 7,
            "token_chat_id": CHAT_ID,
            "token_edit_address": MINT,
        }
    )


# ---------------------------------------------------------------------------
# The explicit delete.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_trader_cannot_delete_a_token(gateway_as):
    gw = gateway_as(ServerPermission.TRADER)
    query = FakeQuery(TRADER_ID)

    await tokens.remove_token(query, _context(), NETWORK, MINT)

    # Refused before anything reached Gateway.
    assert gw.deletes == []
    # And refused *visibly*: a silent no-op would have left the trader looking at
    # a success screen for a delete that never happened.
    assert query.answers == [(tokens.OWNER_REQUIRED_MESSAGE, True)]
    assert query.texts == []


@pytest.mark.asyncio
async def test_the_owner_can_still_delete(gateway_as):
    gw = gateway_as(ServerPermission.OWNER)
    query = FakeQuery(OWNER_ID)

    await tokens.remove_token(query, _context(), NETWORK, MINT)

    assert gw.deletes == [(NETWORK, MINT)]
    assert "Token Removed" in query.texts[0]
    # The permission was checked against the server the delete then landed on.
    assert gw.cm.dialed == [SERVER]


@pytest.mark.asyncio
async def test_a_deletion_names_the_actor_the_network_and_the_address(
    gateway_as, caplog
):
    # SEC-207's audit criterion: who deleted what, from which server's list.
    gateway_as(ServerPermission.OWNER)

    with caplog.at_level("INFO", logger=tokens.logger.name):
        await tokens.remove_token(FakeQuery(OWNER_ID), _context(), NETWORK, MINT)

    line = next(r for r in caplog.records if "Gateway token deleted" in r.message)
    assert f"user_id={OWNER_ID}" in line.message
    assert f"server={SERVER}" in line.message
    assert f"network={NETWORK}" in line.message
    assert f"address={MINT}" in line.message


# ---------------------------------------------------------------------------
# The edit flow, which is a delete underneath.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_trader_cannot_delete_through_the_edit_flow(gateway_as):
    gw = gateway_as(ServerPermission.TRADER)
    update = FakeUpdate(TRADER_ID, "PUMP,6,Pump")
    context = _edit_context()

    await tokens.handle_token_input(update, context)

    assert gw.deletes == []
    assert gw.adds == []
    assert update.bot.texts and "⛔" in update.bot.texts[0]
    # The input state is cleared either way: a refused trader is not left with the
    # next thing they type being read as an edit.
    assert "awaiting_token_input" not in context.user_data


@pytest.mark.asyncio
async def test_the_owner_can_still_edit(gateway_as):
    gw = gateway_as(ServerPermission.OWNER)
    update = FakeUpdate(OWNER_ID, "PUMP,6,Pump")

    await tokens.handle_token_input(update, _edit_context())

    assert gw.deletes == [(NETWORK, MINT)]
    assert gw.adds == [(NETWORK, MINT, "PUMP", 6)]
    assert gw.cm.dialed == [SERVER]
