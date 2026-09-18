"""Which wallet a user calls theirs on a chain, and where that answer lives.

Two wallets can both be "mine" and they are not interchangeable: the browser
one signs only in front of its owner, and Gateway's signs unattended on the
server. Which one a flow reaches for is the user's call, so it is recorded on
the account — the point being that it follows them to their next browser, which
is the whole reason it is not in `localStorage`.

What these pin is the part that is easy to get wrong by accident: attaching a
wallet *is* choosing it (having just proved a key, nobody should have to say so
twice), a choice can never name a browser wallet that is not there, and
detaching takes the choice with it rather than leaving one that points at
nothing.
"""

from __future__ import annotations

import pytest

from condor import paths, wallet_store
from tests.wallet_helpers import attach_wallet as _attach


def test_no_wallet_no_preference():
    assert wallet_store.get_preferred(1) == {}


def test_attaching_a_wallet_is_choosing_it():
    _attach(1)

    assert wallet_store.get_preferred(1) == {"solana": "browser"}


def test_a_chain_can_be_pointed_at_gateway_instead():
    address = _attach(1)

    assert wallet_store.set_preferred(1, "solana", "gateway") == {"solana": "gateway"}
    assert wallet_store.get_preferred(1) == {"solana": "gateway"}
    # And the attachment is still there: preferring Gateway's key is not
    # detaching the browser one, which still runs whatever vaults it runs.
    assert wallet_store.get_wallet(1)["address"] == address


def test_a_chain_with_no_browser_wallet_cannot_prefer_one():
    with pytest.raises(wallet_store.AttachRefused):
        wallet_store.set_preferred(1, "solana", "browser")


def test_an_unknown_source_is_refused_rather_than_stored():
    _attach(1)

    with pytest.raises(wallet_store.AttachRefused):
        wallet_store.set_preferred(1, "solana", "whichever")
    assert wallet_store.get_preferred(1) == {"solana": "browser"}


def test_detaching_forgets_the_preference_with_the_wallet():
    _attach(1)
    wallet_store.set_preferred(1, "solana", "gateway")

    assert wallet_store.detach(1) is True
    # A preference naming a browser wallet nobody here has is worse than none:
    # the file is gone, so the next attach starts from "this key, obviously".
    assert wallet_store.get_preferred(1) == {}


def test_a_damaged_file_reads_as_no_preference_rather_than_failing():
    path = paths.user_dir(1) / "wallet.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")

    assert wallet_store.get_preferred(1) == {}
    assert wallet_store.get_wallet(1) is None


def test_preferences_are_per_user():
    _attach(1)
    _attach(2)
    wallet_store.set_preferred(2, "solana", "gateway")

    assert wallet_store.get_preferred(1) == {"solana": "browser"}
    assert wallet_store.get_preferred(2) == {"solana": "gateway"}


def test_a_wallet_attached_before_the_preference_existed_still_counts_as_chosen():
    """The upgrade case: a `wallet.json` with an address and no `preferred`.

    Reading that as "undecided" would show every existing user no default at
    all on the one chain they have a wallet for, which is a worse answer than
    the one their attachment already gave.
    """
    _attach(1)
    path = paths.user_dir(1) / "wallet.json"
    path.write_text('{"address": "2LjWzppfJyTfTJT3gPAvmk6wukYUwi8cv1CuXjBHhfGj"}')

    assert wallet_store.get_preferred(1) == {}
    assert wallet_store.effective_preferred(1) == {"solana": "browser"}


def test_nothing_is_implied_for_a_user_with_no_wallet():
    assert wallet_store.effective_preferred(1) == {}


def test_a_stated_preference_is_never_overridden_by_the_implied_one():
    _attach(1)
    wallet_store.set_preferred(1, "solana", "gateway")

    assert wallet_store.effective_preferred(1) == {"solana": "gateway"}
