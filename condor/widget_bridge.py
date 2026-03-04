"""Widget Bridge -- TCP server for agent widget IPC.

Renders Telegram inline keyboards on behalf of the agent's MCP tools
and resolves button clicks back to the waiting tool call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

WIDGET_TIMEOUT = 120  # seconds


@dataclass
class _PendingWidget:
    chat_id: int
    message_id: int
    values: list[str]  # button values indexed by position
    future: asyncio.Future


class WidgetBridge:
    """Async TCP server that receives widget requests from MCP tools and
    renders them as Telegram inline keyboards."""

    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._server: asyncio.Server | None = None
        self._port: int = 0
        self._pending: dict[str, _PendingWidget] = {}

    @property
    def port(self) -> int:
        return self._port

    async def start(self, bot: Bot) -> None:
        self._bot = bot
        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", 0
        )
        addr = self._server.sockets[0].getsockname()
        self._port = addr[1]
        log.info("Widget bridge listening on 127.0.0.1:%d", self._port)

    async def stop(self) -> None:
        # Cancel all pending futures
        for widget in list(self._pending.values()):
            if not widget.future.done():
                widget.future.cancel()
        self._pending.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        log.info("Widget bridge stopped")

    # --- TCP handler ---

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            data = await asyncio.wait_for(reader.read(65536), timeout=5)
            if not data:
                return

            request = json.loads(data.decode())
            method = request.get("method", "")

            if method == "send_buttons":
                result = await self._handle_send_buttons(
                    chat_id=request["chat_id"],
                    message=request["message"],
                    buttons=request["buttons"],
                )
            elif method == "send_notification":
                result = await self._handle_send_notification(
                    chat_id=request["chat_id"],
                    message=request["message"],
                )
            else:
                result = {"error": f"Unknown method: {method}"}

            writer.write(json.dumps(result).encode())
            await writer.drain()
        except asyncio.TimeoutError:
            log.warning("Widget bridge: client read timeout")
        except Exception:
            log.exception("Widget bridge connection error")
            try:
                writer.write(json.dumps({"error": "internal error"}).encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # --- Handlers ---

    async def _handle_send_buttons(
        self, chat_id: int, message: str, buttons: list[list[dict]]
    ) -> dict:
        assert self._bot is not None

        request_id = uuid.uuid4().hex[:8]

        # Build keyboard and collect values in order
        values: list[str] = []
        keyboard_rows: list[list[InlineKeyboardButton]] = []

        for row in buttons:
            kb_row: list[InlineKeyboardButton] = []
            for btn in row:
                idx = len(values)
                values.append(btn["value"])
                cb_data = f"agent:w:{request_id}:{idx}"
                kb_row.append(
                    InlineKeyboardButton(btn["label"], callback_data=cb_data)
                )
            keyboard_rows.append(kb_row)

        markup = InlineKeyboardMarkup(keyboard_rows)
        sent = await self._bot.send_message(
            chat_id=chat_id, text=message, reply_markup=markup
        )

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = _PendingWidget(
            chat_id=chat_id,
            message_id=sent.message_id,
            values=values,
            future=future,
        )

        try:
            selected = await asyncio.wait_for(future, timeout=WIDGET_TIMEOUT)
            return {"selected": selected}
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._pending.pop(request_id, None)
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=sent.message_id,
                    text=f"{message}\n\n(timed out)",
                )
            except Exception:
                pass
            return {"timeout": True}

    async def _handle_send_notification(self, chat_id: int, message: str) -> dict:
        assert self._bot is not None
        await self._bot.send_message(chat_id=chat_id, text=message)
        return {"sent": True}

    # --- Resolution (called from Telegram callback handler) ---

    def resolve(self, request_id: str, button_index: int) -> bool:
        """Resolve a pending widget by button index. Returns True if resolved."""
        widget = self._pending.pop(request_id, None)
        if not widget or widget.future.done():
            return False

        if button_index < 0 or button_index >= len(widget.values):
            return False

        value = widget.values[button_index]
        widget.future.set_result(value)

        # Edit message to show selection (fire and forget)
        if self._bot:
            label = value
            asyncio.create_task(
                self._edit_selected(widget.chat_id, widget.message_id, label)
            )

        return True

    async def _edit_selected(
        self, chat_id: int, message_id: int, selected: str
    ) -> None:
        try:
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"Selected: {selected}",
            )
        except Exception:
            pass

    def cancel_for_chat(self, chat_id: int) -> None:
        """Cancel all pending widgets for a chat."""
        to_remove = [
            rid for rid, w in self._pending.items() if w.chat_id == chat_id
        ]
        for rid in to_remove:
            widget = self._pending.pop(rid, None)
            if widget and not widget.future.done():
                widget.future.cancel()


# --- Singleton ---

_bridge: WidgetBridge | None = None


def get_widget_bridge() -> WidgetBridge:
    global _bridge
    if _bridge is None:
        _bridge = WidgetBridge()
    return _bridge
