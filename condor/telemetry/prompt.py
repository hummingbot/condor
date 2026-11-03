"""The telemetry notice, and the callback that answers it.

A notice, not a question: one message next to the "Condor is online"
notification the admin already gets, saying that anonymous usage summaries are
on, what they contain, and how to turn them off. It has a "Got it" button and a
"Turn off" button. It is sent at most once per version until it is delivered,
the attempt is written to disk *before* the message goes out (a crash loop must
not re-send forever), and only a *delivered* notice moves an unanswered install
from the ``ping`` floor to ``usage`` — see :mod:`condor.telemetry.consent`.

Telegram is not the only surface that tells. A local-mode install has no bot to
message, so the dashboard shows the same notice — ``GET
/api/v1/settings/telemetry`` serves :data:`DISCLOSURE` to the notice strip. Both
surfaces render the same copy from the same constant: a privacy claim written
down twice is a privacy claim that will eventually disagree with itself.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "telemetry"

# The two levels an admin can choose in Settings → Privacy, in the order it
# offers them. The notice itself offers "Got it" (usage) and "Turn off" (a
# recorded refusal, `consent.deny`); `off` is not a level here for that reason.
OPTIONS = (
    {"level": "usage", "label": "Usage summaries and install count (default)"},
    {"level": "ping", "label": "Only count my install"},
)

# Everything an install is told.
DISCLOSURE = {
    "headline": "Condor shares anonymous usage stats",
    "summary": (
        "To learn what gets used and what breaks, Condor sends a random install "
        "id, its version, which commands and screens are used, errors, and "
        "which models agents run. Nothing about you or your trading."
    ),
    "opt_out": (
        "You can turn this off at any time in Settings \u2192 Privacy, or with "
        "CONDOR_TELEMETRY=off."
    ),
    # A list in the browser, one sentence in Telegram. The last entry is phrased
    # to close the sentence :func:`_never_line` builds.
    "never": [
        "API keys",
        "wallet addresses",
        "server names or URLs",
        "trading pairs",
        "amounts",
        "balances",
        "positions",
        "prompts or agent replies",
        "Telegram id or username",
    ],
    "doc": (
        "Full details in PRIVACY.md at the root of the repo, which also says "
        "how to change this at any time."
    ),
    "acknowledge": "Got it",
    "turn_off": "Turn off",
    "options": [dict(option) for option in OPTIONS],
}


def _never_line() -> str:
    """The ``never`` list as the one sentence Telegram has always sent."""
    items = DISCLOSURE["never"]
    return f"Never included: {', '.join(items[:-1])}, and no {items[-1]}."


_TEXT = "\n\n".join(
    (
        DISCLOSURE["headline"],
        DISCLOSURE["summary"],
        _never_line(),
        DISCLOSURE["opt_out"],
        DISCLOSURE["doc"],
    )
)


def keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from condor.telemetry import consent

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    DISCLOSURE["acknowledge"],
                    callback_data=f"{CALLBACK_PREFIX}:{consent.USAGE}",
                ),
                InlineKeyboardButton(
                    DISCLOSURE["turn_off"],
                    callback_data=f"{CALLBACK_PREFIX}:{consent.OFF}",
                ),
            ]
        ]
    )


async def maybe_prompt_admin(bot) -> bool:
    """Tell the admin once, if there is anything to tell. Never raises."""
    try:
        from utils.config import ADMIN_USER_ID

        if not ADMIN_USER_ID:
            return False

        from condor.telemetry import consent, context

        version = context.version()
        if not consent.should_prompt(version):
            return False

        # Written first: if sending or the process dies right after, the admin
        # is told again on the next version, not on the next boot loop.
        consent.mark_prompted(version)
        await bot.send_message(
            chat_id=int(ADMIN_USER_ID), text=_TEXT, reply_markup=keyboard()
        )
        # Only a delivered notice turns usage on. A send that raised above
        # leaves the install at the ping floor.
        consent.mark_notice_shown()
        return True
    except Exception:  # noqa: BLE001
        log.debug("Could not send the telemetry notice", exc_info=True)
        return False


async def callback_handler(update, context) -> None:
    """Handle ``telemetry:usage|off`` from the notice, and ``ping`` from a
    prompt an older build sent. Admin only — it is an install-wide setting, and
    the admin owns the install."""
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
        # "Turn off" on the notice — or "No thanks" on a prompt an older build
        # sent. Either is a refusal, so it is recorded as one — the same answer
        # the dashboard's off switch gives — rather than being rounded up to the
        # floor. Anything else unrecognized still lands on ping via grant().
        if answer == consent.OFF:
            consent.deny()
            await query.edit_message_text(
                "Understood. Condor will report nothing at all, and will not "
                "ask again. Settings \u2192 Privacy can turn it back on."
            )
            return

        chosen = consent.grant(answer)
        from condor.telemetry import emitter

        # Almost always a no-op: boot counted this install already. It stays
        # here for the install whose first boot was silenced by
        # `CONDOR_TELEMETRY=off` and which is answering after that was lifted.
        if consent.mark_install_reported():
            emitter.emit("install")
        if chosen == consent.PING:
            await query.edit_message_text(
                "Thanks. Condor will only report that this install exists and "
                "which version it runs. PRIVACY.md says how to change this."
            )
        else:
            await query.edit_message_text(
                "Thanks. Condor sends anonymous usage and reliability events. "
                "No keys, addresses, pairs, amounts or prompts ever leave this "
                "machine. Settings \u2192 Privacy turns it off at any time."
            )
    except Exception:  # noqa: BLE001
        log.exception("Telemetry consent callback failed")
