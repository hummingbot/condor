"""The one-tap consent prompt, and the callback that answers it.

Opt-in only works if asking is cheap, so this is a single message with two
buttons next to the "Condor is online" notification the admin already gets. It
is sent at most once per version, the intent is written to disk *before* the
message goes out (a crash loop must not re-ask forever), and until it is
answered the install stays at the ``ping`` floor — counted, nothing more.

Telegram is not the only surface that asks. A local-mode install has no bot to
message, so the dashboard asks instead — ``GET /api/v1/settings/telemetry``
serves :data:`DISCLOSURE` to the consent card. Both surfaces render the same
copy from the same constant: a privacy claim written down twice is a privacy
claim that will eventually disagree with itself.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CALLBACK_PREFIX = "telemetry"

# The two answers, in the order both surfaces offer them. `off` is deliberately
# absent — install counting is the floor (see ``consent.ANSWER_LEVELS``).
OPTIONS = (
    {"level": "usage", "label": "Yes, share usage summaries"},
    {"level": "ping", "label": "Only count my install"},
)

# Everything an install is told before it answers.
DISCLOSURE = {
    "headline": "Help improve Condor?",
    "always_on": (
        "Condor counts installs so the project knows it is used: a random id, "
        "the version, and an uptime ping — nothing about you or your trading. "
        "That is always on."
    ),
    "optional": (
        "It can also send an anonymous, allowlisted usage summary: which "
        "commands and screens get used, what breaks, and which models agents "
        "run. That part is up to you."
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
        "how to change this answer at any time."
    ),
    "options": [dict(option) for option in OPTIONS],
}


def _never_line() -> str:
    """The ``never`` list as the one sentence Telegram has always sent."""
    items = DISCLOSURE["never"]
    return f"Never included: {', '.join(items[:-1])}, and no {items[-1]}."


_TEXT = "\n\n".join(
    (
        DISCLOSURE["headline"],
        DISCLOSURE["always_on"],
        DISCLOSURE["optional"],
        _never_line(),
        DISCLOSURE["doc"],
    )
)


def keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    option["label"],
                    callback_data=f"{CALLBACK_PREFIX}:{option['level']}",
                )
            ]
            for option in OPTIONS
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
        # An "off" tap can only come from a prompt sent by an older version,
        # where the button read "No thanks". That is a refusal, so it is
        # recorded as one — the same answer the dashboard's off switch gives —
        # rather than being rounded up to the floor. Anything else
        # unrecognized still lands on ping via grant().
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
                "Thanks. Condor will send anonymous usage and reliability "
                "events. No keys, addresses, pairs, amounts or prompts ever "
                "leave this machine. PRIVACY.md says how to change or withdraw "
                "this."
            )
    except Exception:  # noqa: BLE001
        log.exception("Telemetry consent callback failed")
