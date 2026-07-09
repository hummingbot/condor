"""Connect a Hyperliquid wallet via WalletConnect (agent-wallet + builder-fee approval).

This is the WalletConnect counterpart to the web dashboard's browser-extension-only
"Connect Hyperliquid" flow (frontend/src/components/settings/ConnectHyperliquid.tsx):
it lets a user with only a mobile wallet (no MetaMask/Rabby desktop extension)
authorize a trade-only Hyperliquid agent wallet and the Condor builder fee by
scanning a QR code / opening a link on their phone -- and lets that flow be
triggered from chat (Telegram, Claude Code, ...) instead of only the dashboard.
See docs/architecture/hyperliquid-walletconnect-spike.md for the design.
"""

import io
import logging
from urllib.parse import quote

import httpx

from condor.walletconnect import generate_qr_png
from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.settings import settings

logger = logging.getLogger("condor.mcp")


async def _send_qr_photo(uri: str) -> bool:
    """Best-effort: push the pairing URI as a scannable QR photo to Telegram.

    No-ops (returns False) if Telegram isn't configured -- e.g. a Claude Code
    session with no bot token/chat id. Callers should fall back to showing the
    raw `uri` as text in that case.
    """
    if not settings.bot_token or not settings.chat_id:
        return False
    try:
        buf = io.BytesIO(generate_qr_png(uri))
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{settings.bot_token}/sendPhoto",
                data={"chat_id": settings.chat_id, "caption": "Scan to connect Hyperliquid"},
                files={"photo": ("walletconnect.png", buf, "image/png")},
            )
            return resp.json().get("ok", False)
    except Exception:
        logger.exception("Failed to send WalletConnect QR photo to Telegram")
        return False


async def connect_hyperliquid_wallet(action: str, session_id: str = "", server: str = "") -> dict:
    action = (action or "").lower()

    if action == "start":
        server = server or settings.active_server
        if not server:
            return {
                "error": "No active server. Pass server=<name> or set CONDOR_SERVER_NAME.",
            }
        result = await call_main_api(
            "POST", f"/settings/walletconnect/hyperliquid?server={quote(server)}", timeout=30
        )
        if isinstance(result, dict) and result.get("uri"):
            sent = await _send_qr_photo(result["uri"])
            result["qr_sent_to_telegram"] = sent
            result["next_steps"] = (
                "Tell the user to scan the QR (sent to Telegram) or open the 'uri' "
                "field directly in a WalletConnect-compatible mobile wallet if it "
                "wasn't sent (e.g. a non-Telegram session). Once they approve both "
                "signature requests on their phone, poll with "
                'connect_hyperliquid_wallet(action="get", session_id="...").'
            )
        return result

    if action == "get":
        if not session_id:
            return {"error": "session_id is required for get"}
        return await call_main_api("GET", f"/settings/walletconnect/hyperliquid/{session_id}")

    return {"error": f"Unknown action '{action}'. Use start | get."}
