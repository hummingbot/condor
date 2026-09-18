"""The crank's start checks.

Every rule here exists because breaking it does something specific and bad, and
the point of testing them without a chain, a Gateway or an engine is that the
list stays readable: a check nobody can state plainly is a check that will drift.
"""

import pytest

from condor.vault_config import config_hash
from condor.vaults_crank import (
    StartRefused,
    _account_name,
    _native_lamports,
    _snake,
    check_can_run,
)

CONFIG = {"pair": "SOL-USDC", "amount_lamports": 1_000_000}
HASH = config_hash(CONFIG)


def record(**overrides):
    base = {
        "delegate": {"address": "DELEGATE1", "granted_at": 0},
        "pin": {
            "version": 3,
            "config_hash": HASH,
            "fee_bps": 5000,
            "config": CONFIG,
            "scan": {"passed": True, "findings": [], "at": 0},
        },
    }
    base.update(overrides)
    return base


def chain(**overrides):
    base = {
        "state": "Running",
        "delegate": "DELEGATE1",
        "version": 3,
        "config_hash": HASH,
        "fee_bps": 5000,
    }
    base.update(overrides)
    return base


def test_a_healthy_vault_runs():
    check_can_run(record(), chain())


@pytest.mark.parametrize("state", ["Paused", "WindingDown", "Redeemable"])
def test_only_running_runs(state):
    """The runner's pause is not advisory."""
    with pytest.raises(StartRefused) as exc:
        check_can_run(record(), chain(state=state))
    assert state in str(exc.value)


def test_no_delegate_means_nothing_can_sign():
    with pytest.raises(StartRefused) as exc:
        check_can_run(record(), chain(delegate=None))
    assert "sign" in str(exc.value)


def test_a_replaced_delegate_stops_condor_running_it():
    """The runner installed somebody else's key. That is a revoke, and the
    crank has to notice it as one rather than fail transaction by transaction."""
    with pytest.raises(StartRefused) as exc:
        check_can_run(record(), chain(delegate="SOMEONE_ELSE"))
    assert "someone else" in str(exc.value)


@pytest.mark.parametrize("field,value", [("version", 4), ("fee_bps", 1000)])
def test_the_chain_wins_any_disagreement(field, value):
    """Running on a stale copy would mean running parameters the runner did not
    sign — which is the entire reason a hash is on chain."""
    with pytest.raises(StartRefused):
        check_can_run(record(), chain(**{field: value}))


def test_a_config_that_does_not_hash_to_the_chains_is_refused():
    """The check that makes the commitment mean something against Condor's own
    operators: the stored values are believed only because they hash to what
    the runner signed."""
    tampered = record()
    tampered["pin"] = {
        **tampered["pin"],
        "config": {**CONFIG, "amount_lamports": 999_999_999},
    }
    with pytest.raises(StartRefused) as exc:
        check_can_run(tampered, chain())
    assert "not the config this vault signed" in str(exc.value)


def test_a_vault_condor_has_no_config_for_is_skipped_not_guessed():
    missing = record()
    missing["pin"] = {**missing["pin"], "config": None}
    with pytest.raises(StartRefused) as exc:
        check_can_run(missing, chain())
    assert "only its hash" in str(exc.value)


def test_condors_own_check_gates_condors_own_crank():
    failed = record()
    failed["pin"] = {
        **failed["pin"],
        "scan": {"passed": False, "findings": ["slippage_bps is 5000"], "at": 0},
    }
    with pytest.raises(StartRefused) as exc:
        check_can_run(failed, chain())
    assert "slippage_bps is 5000" in str(exc.value)


def test_an_unscanned_version_does_not_run():
    unscanned = record()
    unscanned["pin"] = {**unscanned["pin"], "scan": None}
    with pytest.raises(StartRefused):
        check_can_run(unscanned, chain())


# ── small helpers, pinned because a silent mistake in them is invisible ──


def test_each_vault_gets_its_own_account_name():
    """Two vaults sharing an account would share Gateway's wallet binding, and
    then share a wallet on chain."""
    a = _account_name("3wm644fHe3ekULLCPov4vkDeVCJEaQmnCfn5k2HhMS4B")
    b = _account_name("2W8pm3JijgRgHVvMUoDnRsaus3Mu4SyBJQRLpmQq2XPJ")
    assert a != b
    assert a.replace("_", "").isalnum()


def test_camel_case_from_gateway_reads_as_snake():
    assert _snake({"swigAccount": "A", "feeBps": 1, "state": "Running"}) == {
        "swig_account": "A",
        "fee_bps": 1,
        "state": "Running",
    }


def test_the_delegates_balance_is_read_in_lamports():
    assert _native_lamports({"balances": {"SOL": 0.05}}) == 50_000_000
    assert _native_lamports({"balances": {}}) is None
