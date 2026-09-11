"""The dashboard's own mutations, written down (FEAT-105).

The fourth door. A tick's deeds reach ``actions.jsonl`` through the folded tool
calls it made; a route has no tool call to fold — it has a typed request body,
which is a *better* record rather than a worse one. So a route states its verb
and its line directly, in the vocabulary the log already speaks, and the two
kinds of row join without anything downstream learning a new shape.

One helper rather than a ``deeds`` import in every route module, because the
enumeration test in ``tests/test_deeds.py`` reads route sources looking
for exactly this call: a new mutating route that does not make it fails the
suite, which is the whole answer to "somebody adds a sixth door and nobody
wires it".
"""

from __future__ import annotations

from condor.agents import deeds
from condor.web.models import WebUser


def record_ui_deed(
    user: WebUser | None,
    *,
    verb: str,
    summary: str,
    subject: str = "",
) -> None:
    """Record one dashboard mutation under the acting user. Never raises.

    Called only after the upstream call returned, so a row means the thing
    happened. The acting person is the directory the record lands in
    (:func:`condor.paths.ui_dir`), which no route can forget to supply.
    """
    deeds.record_direct(
        deeds.for_ui(getattr(user, "id", None)),
        verb=verb,
        summary=summary,
        subject=subject,
    )
