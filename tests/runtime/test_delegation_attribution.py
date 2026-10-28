"""A delegated worker is told the ``controller_id`` its positions must carry (condor#302).

``conversation_attribution``'s sibling for the other unattended door: a
background delegation has no permission gate at all (FEAT-032's worker seat
builds none), so the tag handed into its opening context is the only thing
that can ever attribute a position it opens. Unlike the conversation case, a
missing tag is never silence here -- it is the bug report says so explicitly,
so the model does not go looking for one and invent the parent conversation's.
"""

from __future__ import annotations

from condor.runtime.context import delegation_attribution


def test_a_delegation_is_told_the_tag_its_positions_carry():
    block = delegation_attribution("solana_dex_lp_expert.delegation_task-abc123")

    assert 'controller_id="solana_dex_lp_expert.delegation_task-abc123"' in block
    assert "create_*_executor" in block
    assert "this delegation" in block


def test_a_delegation_with_no_tag_is_told_so_explicitly_not_left_silent():
    """The conversation sibling answers no-tag with ``""``; this must not.

    A delegation with no assigned ``controller_id`` is exactly the failure
    condor#302 reports -- a background worker that goes looking for one, finds
    nothing, and either stalls or invents a plausible-looking id of its own. An
    explicit statement is what heads off the invented id; silence is what
    caused it in the first place.
    """
    block = delegation_attribution("")

    assert block != ""
    assert "no controller_id has been assigned" in block.lower()
    assert "do not invent" in block.lower()


def test_no_tag_message_warns_off_reusing_another_sessions_id():
    """The exact failure mode reported: borrowing the parent conversation's tag."""
    block = delegation_attribution("")

    assert "do not" in block.lower()
    assert "reuse" in block.lower() or "invent" in block.lower()
