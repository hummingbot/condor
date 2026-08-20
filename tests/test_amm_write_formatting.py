"""An AMM add or remove has to say what it moved.

The formatter's add_liquidity/remove_liquidity branch printed three lines — position, tx,
status — and nothing else, so a close that moved 3188 DOGE-1 and 0.0219 SOL reported
neither. hummingbot-api returns those amounts (they reach its database, which is how a
4x rent-inflation bug was found), and the CLMM branch twenty lines earlier renders the
same three kinds of money properly. AMM was the odd one out.

The tx line was wrong too: it read `transaction_hash`, a field of the CLMM models.
AMMTransactionResponse carries `signature`, so every AMM write printed "Tx: None".
"""

from mcp_servers.hummingbot_api.formatters.gateway import format_amm_result


def _result(action, data):
    return {
        "action": action,
        "connector": "meteora",
        "network": "solana-mainnet-beta",
        "pool_address": "pool-addr",
        "position_address": "F1YcTMd6BEKAjECLSvZJasXiWTwMbNRTx5fmDwfcm6N5",
        "result": {"signature": "67ZzMAHv", "status": "CONFIRMED", "data": data},
    }


REMOVED = {
    "fee": 0.000013214,
    "baseTokenAmountRemoved": 3188.755913808,
    "quoteTokenAmountRemoved": 0.005299128,
    "positionRentRefunded": 0.0119364,
}


def test_a_removal_reports_the_amounts_it_withdrew():
    out = format_amm_result("remove_liquidity", _result("remove_liquidity", REMOVED))

    assert "3188.755913808" in out
    assert "0.005299128" in out


def test_a_removal_separates_rent_from_liquidity():
    # Two different kinds of money: the rent was locked at open and is not a withdrawal.
    out = format_amm_result("remove_liquidity", _result("remove_liquidity", REMOVED))

    assert "0.0119364" in out
    assert "rent" in out.lower()


def test_the_transaction_is_identified_by_its_signature():
    out = format_amm_result("remove_liquidity", _result("remove_liquidity", REMOVED))

    assert "67ZzMAHv" in out
    assert "None" not in out


def test_an_add_reports_what_it_deposited():
    out = format_amm_result(
        "add_liquidity",
        _result(
            "add_liquidity",
            {
                "fee": 0.00001,
                "baseTokenAmountAdded": 3000.0,
                "quoteTokenAmountAdded": 0.01522083,
                "positionRent": 0.0099,
            },
        ),
    )

    assert "3000.0" in out
    assert "0.01522083" in out
    assert "0.0099" in out


def test_a_partial_removal_omits_a_rent_refund_it_did_not_get():
    # Removing less than everything leaves the position account open, so no rent comes
    # back. Printing "Position rent refunded: None" would read as a missing number
    # rather than an absent event.
    out = format_amm_result(
        "remove_liquidity",
        _result("remove_liquidity", {"baseTokenAmountRemoved": 1.0, "quoteTokenAmountRemoved": 2.0}),
    )

    assert "rent" not in out.lower()
    assert "1.0" in out


def test_a_submitted_write_still_formats():
    # No data at all until the transaction confirms.
    out = format_amm_result("add_liquidity", _result("add_liquidity", None))

    assert "SUBMITTED" in out or "CONFIRMED" in out


# The CLMM surface has the same gap and a different shape: /clmm/add and /clmm/remove
# return a flat snake_case dict with `transaction_hash`, not an AMMTransactionResponse
# wrapping Gateway's camelCase `data`. Both branches now print the amounts; neither may
# be "corrected" into the other's key convention.
from mcp_servers.hummingbot_api.formatters.gateway import format_clmm_result


def _clmm(action, payload):
    return {
        "action": action,
        "connector": "orca",
        "network": "solana-mainnet-beta",
        "result": payload,
    }


def test_a_clmm_removal_reports_the_amounts_it_withdrew():
    out = format_clmm_result(
        "remove_liquidity",
        _clmm(
            "remove_liquidity",
            {
                "transaction_hash": "4Rg7buez",
                "position_address": "4G5GyCPi",
                "base_token_amount_removed": 0.0101,
                "quote_token_amount_removed": 6.095924,
                "gas_fee": 0.00001,
                "status": "confirmed",
            },
        ),
    )

    assert "0.0101" in out
    assert "6.095924" in out
    assert "4Rg7buez" in out


def test_a_clmm_add_reports_what_it_deposited():
    out = format_clmm_result(
        "add_liquidity",
        _clmm(
            "add_liquidity",
            {
                "transaction_hash": "5aMZatkF",
                "position_address": "4G5GyCPi",
                "base_token_amount_added": 3000.0,
                "quote_token_amount_added": 0.01522083,
                "status": "confirmed",
            },
        ),
    )

    assert "3000.0" in out
    assert "0.01522083" in out
