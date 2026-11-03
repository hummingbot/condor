"""Sharing a conversation with the project (FEAT-054, FEAT-055).

A user hands one of their own chats to Condor so that real transcripts —
prompts, agent replies, tool trajectories — accumulate into a corpus we can
read to make the agents better. The default is that nothing leaves: sharing is
off, silence is never read as yes, and the only producer is a button somebody
pressed.

There is one way to change that, and only the user themselves can: choosing
**Always** in Settings → Privacy lets :mod:`condor.sharing.sweep` take their
finished conversations without asking again. That path is deliberately narrower
than the button — forward-only from the moment of the choice, single-author
only, per-conversation exclusion honoured forever, and a chip in the chat header
that cannot be dismissed while it is on. Read that module's docstring before
changing anything here: it is the one place where the scrubber is the last gate
rather than the second-to-last.

**This package never imports** :mod:`condor.telemetry.schema` **and never
writes the collector's** ``events`` **table.** That rule is the whole reason
the package exists rather than being a new telemetry event, and
``tests/test_sharing_scrub.py`` asserts it. ``condor/telemetry/`` is where
"free text cannot escape" is enforced by construction — an allowlisted
taxonomy, a 64-character cap, a character class — and widening it to carry a
transcript would delete the one property that makes ``PRIVACY.md`` checkable
rather than merely claimed. Sharing is honestly a different thing: it is
*content*, and its safety comes from a different mechanism — a scrubber whose
output the user is shown before they consent.

The patterns are reused where they fit and only where they fit. The durable
outbox, the deployment context block and the settings surface all come from
telemetry's shape; the consent record, the identity, the queue file and the
wire schema are this package's own, because the unit of consent is the *user*
and the unit of telemetry consent is the *install*.

Public surface::

    from condor import sharing
    preview = sharing.preview(user_id, conv_id)   # what would be sent
    receipt = sharing.submit(user_id, conv_id)    # send exactly that
    sharing.unshare(user_id, conv_id)             # take it back
    sharing.unshare_all(user_id)                  # take all of it back

The automatic producer is reached through its module rather than re-exported
here — ``from condor.sharing import sweep`` then ``await sweep.sweep()``. Hoisting
the function to this namespace would bind the name to the function and shadow the
module of the same name for every other importer, including the HTTP routes that
call ``sweep.covered()``.
"""

from condor.sharing.consent import (
    ALWAYS,
    EXPLICIT,
    OFF,
    can_share,
    can_sweep,
    opted_in_at,
    user_state,
)
from condor.sharing.share import preview, submit, unshare, unshare_all

__all__ = [
    "preview",
    "submit",
    "unshare",
    "unshare_all",
    "can_share",
    "can_sweep",
    "user_state",
    "opted_in_at",
    "OFF",
    "EXPLICIT",
    "ALWAYS",
]
