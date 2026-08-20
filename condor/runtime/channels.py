"""Delivery channels for pending trade confirmations (ARCH-190).

The pending approval itself lives in :mod:`condor.runtime.confirmations`; a
channel only renders it somewhere a human can answer. ``TelegramChannel`` moved
here from ``handlers/agents/confirmation.py`` so the runtime (consult,
delegate) no longer imports from the hot-reloaded handlers package; that module
re-exports it for its Telegram callers.
"""

from __future__ import annotations

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from condor.runtime.confirmations import PendingConfirmation


class TelegramChannel:
    """Renders a pending confirmation as an inline keyboard in a chat."""

    def __init__(self, bot: Bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def deliver(self, pending: PendingConfirmation) -> None:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Approve", callback_data=f"agent:confirm_trade:{pending.id}"
                    ),
                    InlineKeyboardButton(
                        "Reject", callback_data=f"agent:reject_trade:{pending.id}"
                    ),
                ]
            ]
        )
        # Naming the asker is not decoration: one chat can talk to several
        # agents on several servers, and "Approve this action?" on its own does
        # not say whose action, or where it lands.
        asked_by = f"\nRequested by {pending.origin}\n" if pending.origin else ""
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=(
                f"Trade Confirmation\n{asked_by}\n"
                f"{pending.summary}\n\nApprove this action?"
            ),
            reply_markup=keyboard,
        )
