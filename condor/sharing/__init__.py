"""Sharing a conversation with the project (FEAT-054).

A user hands one of their own chats to Condor so that real transcripts —
prompts, agent replies, tool trajectories — accumulate into a corpus we can
read to make the agents better. Nothing here happens without somebody pressing
a button: there is no default, no silence-means-yes and no background producer.

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
"""

from condor.sharing.consent import ALWAYS, EXPLICIT, OFF, can_share, user_state
from condor.sharing.share import preview, submit, unshare

__all__ = [
    "preview",
    "submit",
    "unshare",
    "can_share",
    "user_state",
    "OFF",
    "EXPLICIT",
    "ALWAYS",
]
