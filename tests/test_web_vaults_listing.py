"""A private vault has no mint, and the listing has to survive that.

Most vaults are private — no token, no outside holders — and that is a finished
state rather than an unfinished one. The chain model typed `mint` and
`dbc_pool` as required strings, so the row for every such vault failed
validation and `GET /vaults` answered 500: the page was broken for exactly the
vaults the product is mostly about, and only for people who had one.

Also pins that a vault nobody here has a record of still lists. A vault's
creator, state, phase and launch terms are public, so the chain is the list and a
local record only adds what its owner has.
"""

from condor.web.models import VaultChainState
from condor.web.routes.vaults import _chain_only_info, _to_info

PRIVATE_CHAIN = {
    "creator": "2LjWzppfJyTfTJT3gPAvmk6wukYUwi8cv1CuXjBHhfGj",
    "account": "4YfP3qnEiNeof88jsBE3LpeiV8M1VFPBtyZejmhQXaer",
    "treasury": "3xV8qNoHiNCopjSmdqt83c1Gg5VgZTWd36ENZqWf6Vd6",
    "mint": None,
    "dbcPool": None,
    "configHash": "ab" * 32,
    "quoteMint": "So11111111111111111111111111111111111111112",
    "version": 1,
    "state": "Running",
    "delegate": "BSiYJDBLDSxuCTBx4caLLtNoFzcyt6HHpwx5ukvizzeT",
    "createdTs": 1789696000,
    "dammPool": None,
}

RECORD = {
    "label": "Cover LP",
    "server": "vaults",
    "network": "mainnet-beta",
    "treasury_address": PRIVATE_CHAIN["treasury"],
    "creator_address": PRIVATE_CHAIN["creator"],
    "quote_mint": PRIVATE_CHAIN["quoteMint"],
    "delegate": {"address": PRIVATE_CHAIN["delegate"], "granted_at": 1},
    "token": None,
    "pin": {"config_hash": "ab" * 32, "version": 1, "config": {}},
    "created_at": 1789696000,
}


def test_a_private_vault_has_no_mint_and_still_validates():
    state = VaultChainState(
        creator=PRIVATE_CHAIN["creator"],
        treasury=PRIVATE_CHAIN["treasury"],
        mint=None,
        dbc_pool=None,
        config_hash=PRIVATE_CHAIN["configHash"],
        state="Running",
    )
    assert state.mint is None
    assert state.tokenized is False


def test_a_record_for_a_private_vault_becomes_a_row():
    row = _to_info(PRIVATE_CHAIN["account"], {**RECORD, "chain": _snake_private()})
    assert row.live is True
    assert row.label == "Cover LP"
    assert row.chain is not None and row.chain.mint is None


def test_a_vault_with_no_record_still_lists():
    """Somebody else's vault, or one created from another install: the chain
    has it, so the listing has it — with nothing of anyone's private record."""
    row = _chain_only_info(PRIVATE_CHAIN["account"], PRIVATE_CHAIN, "vaults")
    assert row.account == PRIVATE_CHAIN["account"]
    assert row.creator_address == PRIVATE_CHAIN["creator"]
    assert row.treasury_address == PRIVATE_CHAIN["treasury"]
    # `live` is true because the account exists: the flag asks whether the
    # create transaction landed, which for a vault read off the chain is
    # already answered.
    assert row.live is True
    assert row.label == ""
    assert row.pin is None
    assert row.token is None


def _snake_private() -> dict:
    from condor.web.routes.vaults import _chain_fields

    return _chain_fields(PRIVATE_CHAIN)
