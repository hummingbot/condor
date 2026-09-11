"""Claiming a bot whose ownership claim never landed.

A session records what it deployed in ``owned_bots.json``, and every money
surface downstream — ``condor.agents.attribution``, the strategy's performance
panel, the fleet browser's tree — keys on that file. When the claim is lost the
fleet trades on unattributed and the agent reports ``+$0.00`` while ``/bots``
shows the controllers live with real volume.

It was lost routinely: over the ACP bridge a tool call's arguments arrive as
``rawInput``, and until that was translated at the boundary nothing could see
*which* bot a ``manage_bots`` call had deployed. Observed on
``brigado/pmm_king_btc_brl_fleet_operator`` session 1 — 36 recorded actions,
every one of them ``"manage_bots (arguments could not be read)"``, no
``owned_bots.json`` at all, and a six-controller fleet trading unattributed.

That is fixed for new runs. This is the door for the ones that already happened,
and these tests pin the two properties that make it a *repair* rather than a
rename: the window opens when the bot was deployed, and a claim survives being
read back by the code that scores attribution.
"""

from pathlib import Path

import pytest

from condor.agents.ownership import (
    BotLedger,
    bot_namespace,
    read_owned,
    strip_deploy_suffix,
)


def _session(tmp_path: Path) -> Path:
    session_dir = (
        tmp_path
        / "brigado"
        / "strategies"
        / "pmm_king_btc_brl_fleet_operator"
        / "sessions"
        / "session_1"
    )
    session_dir.mkdir(parents=True)
    return session_dir


def _claim(session_dir: Path, name: str, since: float) -> BotLedger:
    """What the route does, minus FastAPI: adopt a bot outside the namespace."""
    namespace = bot_namespace("brigado", "pmm_king_btc_brl_fleet_operator")
    base = strip_deploy_suffix(name)
    ledger = BotLedger(namespace, session_dir, declared=[base], enforced=False)
    ledger.adopt(base, since)
    return ledger


# ── The claim itself ──


def test_a_bot_outside_the_namespace_can_be_claimed(tmp_path):
    """The case that matters, and the one the namespace rule cannot serve.

    ``brigado``'s fleet deployed as ``pmm-king-btcbrl-…`` — nothing like its
    namespace ``brigado-pmm_king_btc_brl_fleet_operator`` — and the session ran
    with ``bot_name: ''``, so there was no declared name either. Neither
    automatic rule could ever have claimed it; a person naming it is the only
    evidence there is.
    """
    session_dir = _session(tmp_path)
    _claim(session_dir, "pmm-king-btcbrl-20260903-181000", since=1_788_448_200.0)

    owned = read_owned(session_dir)
    assert [bot.base for bot in owned] == ["pmm-king-btcbrl"]


def test_the_deploy_suffix_is_stripped(tmp_path):
    """The running instance and the base name are the same claim.

    A reader copies the name off the fleet page, which carries the suffix a
    deploy appends. Claiming that verbatim would record a base no attribution
    rule matches.
    """
    session_dir = _session(tmp_path)
    _claim(session_dir, "pmm-king-btcbrl-20260903-181000", since=1_788_448_200.0)

    assert read_owned(session_dir)[0].base == "pmm-king-btcbrl"


def test_the_window_opens_when_the_bot_was_deployed(tmp_path):
    """The load-bearing parameter.

    The ledger slices PnL over the window it owns a bot for. Claiming at "now"
    credits the strategy with nothing it has already made — which on a fleet
    that has been trading for fourteen hours is the same ``$0.00`` the claim was
    meant to fix.
    """
    session_dir = _session(tmp_path)
    deployed_at = 1_788_448_200.0

    _claim(session_dir, "pmm-king-btcbrl", since=deployed_at)

    bot = read_owned(session_dir)[0]
    assert bot.since == deployed_at
    # Still owned: an open window has no end.
    assert bot.until == 0.0


def test_claiming_is_idempotent_and_keeps_the_first_window(tmp_path):
    """A second claim must not restart the clock it just set.

    ``_record``'s "first claim wins" rule, exercised through the door a person
    can press twice.
    """
    session_dir = _session(tmp_path)
    _claim(session_dir, "pmm-king-btcbrl", since=1_788_448_200.0)
    _claim(session_dir, "pmm-king-btcbrl", since=1_788_500_000.0)

    owned = read_owned(session_dir)
    assert len(owned) == 1
    assert owned[0].since == 1_788_448_200.0


def test_a_claim_never_downgrades_a_bot_the_session_deployed(tmp_path):
    """A recorded deploy stays a deploy.

    The two claims coexist by design — the risk gate claims on the way in and
    the folded tool call claims on the way out — and a manual repair must not
    rewrite a session's own evidence as a hand-over.
    """
    session_dir = _session(tmp_path)
    namespace = bot_namespace("brigado", "pmm_king_btc_brl_fleet_operator")
    ledger = BotLedger(namespace, session_dir, enforced=False)
    ledger.note_deploy("brigado-pmm_king_btc_brl_fleet_operator", 1_788_448_200.0)

    _claim(
        session_dir, "brigado-pmm_king_btc_brl_fleet_operator", since=1_788_500_000.0
    )

    owned = read_owned(session_dir)
    assert owned[0].origin == "deployed"
    assert owned[0].since == 1_788_448_200.0


# ── What reads it back ──


def test_the_claim_survives_a_reopen_by_a_later_reader(tmp_path):
    """Written as owned, and read back as owned by code with no config.

    ``enforced=None`` is how boot reconciliation and the attribution pass reopen
    a finished session's ledger — with nothing to restate the mode from. The
    claim has to survive that, or the repair lasts exactly as long as the
    request that made it.
    """
    session_dir = _session(tmp_path)
    _claim(session_dir, "pmm-king-btcbrl", since=1_788_448_200.0)

    namespace = bot_namespace("brigado", "pmm_king_btc_brl_fleet_operator")
    reopened = BotLedger(namespace, session_dir)

    assert reopened.owns("pmm-king-btcbrl")
    assert reopened.owns("pmm-king-btcbrl-20260903-181000")  # the deployed instance
    assert "pmm-king-btcbrl" in reopened.bases()


def test_claiming_one_bot_does_not_claim_the_server(tmp_path):
    """The narrowing survives too: a stranger's bot stays a stranger's."""
    session_dir = _session(tmp_path)
    reopened = _claim(session_dir, "pmm-king-btcbrl", since=1_788_448_200.0)

    assert not reopened.owns("someone-elses-bot")
    assert not reopened.owns("")


@pytest.mark.parametrize("name", ["", "   ", "-20260903-181000"])
def test_an_empty_name_records_nothing(tmp_path, name):
    """A name that strips to nothing is not a claim on everything."""
    session_dir = _session(tmp_path)
    namespace = bot_namespace("brigado", "pmm_king_btc_brl_fleet_operator")
    ledger = BotLedger(namespace, session_dir, enforced=False)
    ledger.adopt(strip_deploy_suffix(name.strip()), 1_788_448_200.0)

    assert read_owned(session_dir) == []
