"""What Telegram did to the world, written down (CORR-622).

The fifth door, and on this product the busiest one -- ``CLAUDE.md`` opens with
"Condor is a Telegram bot for monitoring and trading with Hummingbot". FEAT-105
wired the chat, the delegations and the dashboard and left this one alone, which
made :attr:`~condor.agents.deed_index.DeedIndex.since` draw a completeness line
the log did not honour: from the first web deed onward, every executor and every
bot a person deployed from Telegram was a record with no deed after the cut, and
``/bots`` called it **Outside Condor**.

A handler is the dashboard's case exactly: no tool call to fold, but arguments
already in hand, so it states its verb and its line directly in the vocabulary
the log already speaks. One helper rather than a ``deeds`` import per handler,
because the enumeration test in ``tests/test_deeds.py`` reads handler sources
looking for exactly this call -- a new deploy path that does not make it fails
the suite.

The acting person is ``update.effective_user``: in a group chat the deed belongs
to whoever pressed the button, not to the room, and it is the same id the
dashboard writes under, so one person's two doors stay one footprint.
"""

from __future__ import annotations

from condor.agents import deeds


def record_telegram_deed(
    update,
    *,
    verb: str,
    summary: str,
    subject: str = "",
    ok: bool = True,
    error: str = "",
) -> None:
    """Record one Telegram mutation under the acting user. Never raises.

    Called only after the upstream call returned, so a row means the thing was
    asked for and answered. ``ok=False`` still records: a deploy the API refused
    is a deed of Condor's either way, and only a successful one claims the bot
    name (:func:`condor.agents.deeds.record_direct`).
    """
    user = getattr(update, "effective_user", None)
    deeds.record_direct(
        deeds.for_telegram(getattr(user, "id", None)),
        verb=verb,
        summary=summary,
        subject=subject,
        ok=ok,
        error=error,
    )
