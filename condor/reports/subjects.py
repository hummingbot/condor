"""Subject keys: what a report is *about*.

A report's subject is the one thing it was generated for — an archived run, one
controller inside it — as an opaque string stamped on the index entry and matched
exactly by ``list_reports(subject=...)``. Every key is spelled here and nowhere
else: the producer that stamps a report and the caller that looks one up both go
through the same constructor, so the two halves cannot drift apart. Callers that
receive the parts over the wire (a route, a dashboard request) build the key
server-side from those parts rather than accepting a pre-built key.
"""

from __future__ import annotations


def bot_run(server: str, db_path: str, controller_id: str = "") -> str:
    """The subject key for an archived run, or one controller inside it."""
    return "bot_run:" + "|".join((server, db_path, controller_id))
