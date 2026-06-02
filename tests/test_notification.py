"""Tests for MCP send_notification → Telegram API."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from condor.telegram_notify import _TELEGRAM_MAX_MESSAGE_LEN, prepare_agent_notification_text
from mcp_servers.condor.tools import notification

# Realistic agent notification bodies (plain text — no parse_mode).
NOTIFICATION_FORMAT_SAMPLES: list[tuple[str, str]] = [
    ("plain_greeting", "Hello world"),
    ("mdv2_pipes", "BTC-USD | ~$200 notional | 30x"),
    (
        "mdv2_escapes",
        r"CLOSED SHORT ETH-USD \| STOP\_LOSS \| PnL \$+12.50 \| id: pe\_abc123",
    ),
    (
        "agent_tick",
        """📊 TICK #2 — macdbb_scanner_aggressive_hl_30

⚡ OPENED SHORT BTC-USD | ~$200 notional | 30x | SL 1.5% TP 3% market

🔑 Executor ID: pe_abc123

💡 WHY: bullish momentum + midBB touch.""",
    ),
    ("html_entity", "P&amp;L total _~+$6.04_"),
    ("markdown_bold", "**Status:** `ASTER-USD` running"),
    ("special_chars", "_[]()~`>#+-=|{}.!"),
    ("snake_case", "Instance macdbb_scanner_aggressive_hl_15 is running."),
    ("dollar_signs", "PnL $+12.50 | margin ~$6.67 | notional $200"),
    ("unicode", "⚡ CLOSED LONG 币安-USD | TAKE_PROFIT | PnL $+3.21"),
    ("empty", ""),
    ("extra_newlines", "line1\n\n\n\n\nline2"),
    ("long_text", "word " * 1200),
]


def _mock_send(result: dict | None = None):
    mock_response = MagicMock()
    mock_response.json.return_value = result or {"ok": True}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _run_send(text: str, mock_client: AsyncMock) -> dict:
    with patch.object(notification.settings, "bot_token", "test-token"), patch.object(
        notification.settings, "chat_id", 12345
    ), patch("mcp_servers.condor.tools.notification.httpx.AsyncClient", return_value=mock_client):
        return asyncio.run(notification.send_notification(text))


def test_send_notification_plain_payload_no_parse_mode():
    mock_client = _mock_send()
    result = _run_send("Hello \\| world", mock_client)

    assert result == {"sent": True}
    mock_client.post.assert_awaited_once()
    payload = mock_client.post.await_args.kwargs["json"]
    assert "parse_mode" not in payload
    assert payload["chat_id"] == 12345
    assert payload["text"] == "Hello | world"


@pytest.mark.parametrize("name,sample_text", NOTIFICATION_FORMAT_SAMPLES, ids=[s[0] for s in NOTIFICATION_FORMAT_SAMPLES])
def test_send_notification_various_formats_never_use_parse_mode(name: str, sample_text: str):
    mock_client = _mock_send()
    result = _run_send(sample_text, mock_client)

    assert result == {"sent": True}, f"format {name!r} should succeed"
    payload = mock_client.post.await_args.kwargs["json"]
    assert "parse_mode" not in payload
    assert payload["text"] == prepare_agent_notification_text(sample_text)
    assert len(payload["text"]) <= _TELEGRAM_MAX_MESSAGE_LEN


def test_send_notification_missing_token():
    with patch.object(notification.settings, "bot_token", ""), patch.object(
        notification.settings, "chat_id", 12345
    ):
        result = asyncio.run(notification.send_notification("Hello"))

    assert result == {"error": "TELEGRAM_BOT_TOKEN not configured"}


def test_send_notification_telegram_error():
    mock_client = _mock_send(
        {"ok": False, "description": "Bad Request: chat not found"}
    )
    result = _run_send("Hello", mock_client)

    assert result == {"error": "Bad Request: chat not found"}


def _load_integration_credentials() -> tuple[str, int] | None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat_raw = (os.environ.get("CONDOR_CHAT_ID") or os.environ.get("ADMIN_USER_ID") or "").strip()
    if not token or not chat_raw:
        return None
    try:
        chat_id = int(chat_raw)
    except ValueError:
        return None
    return token, chat_id


@pytest.mark.integration
@pytest.mark.parametrize("name,sample_text", NOTIFICATION_FORMAT_SAMPLES, ids=[s[0] for s in NOTIFICATION_FORMAT_SAMPLES])
def test_live_send_notification_formats(name: str, sample_text: str):
    """Send real Telegram messages. Run with: RUN_TELEGRAM_INTEGRATION=1 pytest -m integration."""
    if not os.environ.get("RUN_TELEGRAM_INTEGRATION"):
        pytest.skip("Set RUN_TELEGRAM_INTEGRATION=1 to hit the Telegram API")

    creds = _load_integration_credentials()
    if creds is None:
        pytest.skip("Need TELEGRAM_TOKEN (or TELEGRAM_BOT_TOKEN) and CONDOR_CHAT_ID (or ADMIN_USER_ID)")

    token, chat_id = creds
    body = sample_text.strip()
    if not body:
        body = "[test] empty notification body"
    else:
        body = f"[test:{name}] {body}"

    with patch.object(notification.settings, "bot_token", token), patch.object(
        notification.settings, "chat_id", chat_id
    ):
        result = asyncio.run(notification.send_notification(body))

    assert result == {"sent": True}, result
