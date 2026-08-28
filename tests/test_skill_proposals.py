"""Proposed playbooks — offered by the agent, accepted by a human (FEAT-074).

The whole point of this module is a boundary, so the test that matters most is
the negative one: while a proposal is pending, the agent's injected skills
index, its catalog and its search are exactly what they were before it existed.
Everything else here pins the small contract around that — one pending proposal
per agent, accept is a real ``SkillStore.create``, and a create that failed
leaves the offer standing rather than losing it.
"""

from __future__ import annotations

import pytest

from condor.memory import proposals
from condor.memory.skills import SkillStore

SLUG = "brigado"

FIELDS = {
    "name": "CLMM rebalance",
    "description": "Re-centre a CLMM position when price leaves the range",
    "when_to_use": "The user asks to check or rebalance an LP range",
    "body": "1. Pull the pool state\n2. Compare to the position bounds",
}


@pytest.fixture
def library():
    """The agent's own skill library, with one authored playbook already in it."""
    store = SkillStore(SLUG)
    store.create(
        name="Existing playbook",
        description="Something that already shipped",
        when_to_use="Whenever it applies",
        body="Steps.",
    )
    return store


def _pending_files():
    return sorted(p.name for p in proposals.proposals_root(SLUG).glob("*.md"))


# ── Filing one ──


def test_a_proposal_is_one_file_beside_the_library_naming_its_conversation():
    assert proposals.put(SLUG, **FIELDS, conversation_id="8f2c1a4b90de") == {
        "saved": True,
        "name": "clmm_rebalance",
    }

    assert _pending_files() == ["clmm_rebalance.md"]
    # A sibling of ``skills/``, not a child of it — that is the whole boundary.
    assert proposals.proposals_root(SLUG).parent == SkillStore(SLUG).skills_dir.parent
    assert proposals.get(SLUG)["from_conversation"] == "8f2c1a4b90de"


def test_the_proposal_reads_back_with_everything_the_card_needs():
    proposals.put(SLUG, **FIELDS, conversation_id="abc")

    pending = proposals.get(SLUG)

    assert pending["name"] == "clmm_rebalance"
    assert pending["description"] == FIELDS["description"]
    assert pending["when_to_use"] == FIELDS["when_to_use"]
    assert pending["body"] == FIELDS["body"]
    assert pending["source"] == proposals.SOURCE
    assert pending["created"]


def test_nothing_pending_reads_as_none():
    assert proposals.get(SLUG) is None


def test_a_second_proposal_replaces_the_standing_one():
    proposals.put(SLUG, **FIELDS)
    proposals.put(SLUG, **{**FIELDS, "name": "Range check"})

    assert _pending_files() == ["range_check.md"]
    assert proposals.get(SLUG)["name"] == "range_check"


def test_a_half_filled_proposal_is_refused_and_writes_nothing():
    result = proposals.put(
        SLUG, name="No body", description="d", when_to_use="w", body=""
    )

    assert "error" in result
    assert proposals.get(SLUG) is None


def test_one_agents_proposal_is_not_anothers():
    proposals.put(SLUG, **FIELDS)

    assert proposals.get("someone_else") is None


# ── The boundary ──


def test_a_pending_proposal_is_invisible_to_the_library(library):
    before = (library.list_index(), library.catalog(), library.search(""))

    proposals.put(SLUG, **FIELDS)

    assert (library.list_index(), library.catalog(), library.search("")) == before


def test_a_pending_proposal_cannot_be_read_as_a_skill(library):
    proposals.put(SLUG, **FIELDS)

    assert library.read("clmm_rebalance") is None
    assert library.search("re-centre") == []


# ── Accepting ──


def test_accept_creates_a_real_skill_and_takes_the_proposal_away(library):
    proposals.put(SLUG, **FIELDS)

    assert proposals.accept(SLUG) == {"accepted": True, "name": "clmm_rebalance"}

    skill = library.read("clmm_rebalance")
    assert skill["description"] == FIELDS["description"]
    assert skill["when_to_use"] == FIELDS["when_to_use"]
    assert skill["body"] == FIELDS["body"]
    assert proposals.get(SLUG) is None
    assert not proposals.proposals_root(SLUG).exists()


def test_an_accepted_playbook_is_indistinguishable_from_an_authored_one(library):
    proposals.put(SLUG, **FIELDS)
    proposals.accept(SLUG)

    assert "clmm_rebalance" in library.list_index()
    assert [row["slug"] for row in library.catalog()] == [
        "clmm_rebalance",
        "existing_playbook",
    ]
    # Editable and deletable like any other — nothing about it is special.
    assert library.edit("clmm_rebalance", body="Newer steps.")["body"] == "Newer steps."
    assert library.delete("clmm_rebalance") is True


def test_an_accepted_playbook_is_stamped_as_the_agents_own_proposal(library):
    proposals.put(SLUG, **FIELDS)
    proposals.accept(SLUG)

    path = library.skills_dir / "clmm_rebalance" / "SKILL.md"
    assert f"source: {proposals.SOURCE}" in path.read_text()


def test_a_failed_create_leaves_the_proposal_standing(monkeypatch, library):
    proposals.put(SLUG, **FIELDS)
    monkeypatch.setattr(
        SkillStore, "create", lambda *a, **k: {"error": "the library said no"}
    )

    assert proposals.accept(SLUG) == {"error": "the library said no"}

    assert proposals.get(SLUG)["name"] == "clmm_rebalance"


def test_accepting_nothing_is_an_error_not_a_crash():
    assert "error" in proposals.accept(SLUG)


# ── Discarding ──


def test_discard_removes_the_proposal_and_leaves_the_library_alone(library):
    before = library.catalog()
    proposals.put(SLUG, **FIELDS)

    assert proposals.discard(SLUG) is True

    assert proposals.get(SLUG) is None
    assert library.catalog() == before


def test_discarding_nothing_says_so():
    assert proposals.discard(SLUG) is False
