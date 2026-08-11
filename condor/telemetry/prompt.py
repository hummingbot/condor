"""The one-tap consent prompt, and the callback that answers it.

Opt-in only works if asking is cheap, so this is a single message with three
buttons next to the "Condor is online" notification the admin already gets. It
is sent at most once per version, the intent is written to disk *before* the
message goes out (a crash loop must not re-ask forever), and until it is
answered the install emits nothing at all.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "telemetry"

_TEXT = (
    "Help improve Condor?\n\n"
    "Condor can send an anonymous, allowlisted usage summary to the project: "
    "which commands and screens get used, what breaks, and which models agents "
    "run. It is off right now and stays off unless you say yes.\n\n"
    "Never included: API keys, wallet addresses, server names or URLs, "
    "trading pairs, amounts, balances, positions, prompts or agent replies, "
    "and no Telegram id or username.\n\n"
    "Full details in PRIVACY.md at the root of the repo. "
    "You can change this any time from the dashboard settings."
)


def keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Yes, help improve Condor", callback_data=f"{CALLBACK_PREFIX}:usage"
                )
            ],
            [
                InlineKeyboardButton(
                    "Only count my install", callback_data=f"{CALLBACK_PREFIX}:ping"
                )
            ],
            [InlineKeyboardButton("No thanks", callback_data=f"{CALLBACK_PREFIX}:off")],
        ]
    )


async def maybe_prompt_admin(bot) -> bool:
    """Ask the admin once, if there is anything to ask. Never raises."""
    try:
        from utils.config import ADMIN_USER_ID

        if not ADMIN_USER_ID:
            return False

        from condor.telemetry import consent, context

        version = context.version()
        if not consent.should_prompt(version):
            return False

        # Written first: if sending or the process dies right after, the admin
        # gets asked again on the next version, not on the next boot loop.
        consent.mark_prompted(version)
        await bot.send_message(
            chat_id=int(ADMIN_USER_ID), text=_TEXT, reply_markup=keyboard()
        )
        return True
    except Exception:  # noqa: BLE001
        log.debug("Could not send the telemetry consent prompt", exc_info=True)
        return False


async def callback_handler(update, context) -> None:
    """Handle ``telemetry:usage|ping|off``. Admin only — it is an install-wide
    setting, and the admin owns the install."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:  # noqa: BLE001
        pass

    try:
        from condor.telemetry import consent
        from condor.telemetry.consent import _cm

        user = getattr(update, "effective_user", None)
        cm = _cm()
        if cm is None or user is None or not cm.is_admin(int(user.id)):
            await query.edit_message_text(
                "Only the admin can change the telemetry setting."
            )
            return

        answer = (
            (query.data or "").split(":", 1)[1] if ":" in (query.data or "") else ""
        )
        if answer == "off":
            consent.deny()
            await query.edit_message_text(
                "Telemetry stays off. Nothing was collected, and the local "
                "buffer has been deleted."
            )
            return

        chosen = consent.grant(answer)
        from condor.telemetry import emitter

        emitter.emit("install")
        if chosen == consent.PING:
            await query.edit_message_text(
                "Thanks. Condor will only report that this install exists and "
                "which version it runs. Change it any time in the dashboard "
                "settings; details in PRIVACY.md."
            )
        else:
            await query.edit_message_text(
                "Thanks. Condor will send anonymous usage and reliability "
                "events. No keys, addresses, pairs, amounts or prompts ever "
                "leave this machine. Change it any time in the dashboard "
                "settings; details in PRIVACY.md."
            )
    except Exception:  # noqa: BLE001
        log.exception("Telemetry consent callback failed")
