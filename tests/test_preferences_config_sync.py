"""Every preference write reaches config.yml, and every read hydrates from it.

The sync to ConfigManager used to be opt-in per setter, and six setters never
called it — so the wallets, DEX pools and executor defaults chosen in Telegram
existed only in the pickle. The sharing scrubber reads the config.yml copy to
learn which wallets are the user's own, so those wallets were never redacted
(ARCH-604). Hydration had the mirror hole: only two of ~15 accessors called it,
so a cold pickle read defaults over a populated config.yml.

Both directions are now structural: writes go through ``_mutate``, reads through
``_migrate_legacy_data``.
"""

import inspect

import pytest

import condor.preferences as prefs_module
import config_manager as cm_module
from condor.preferences import (
    DEFAULT_PORTFOLIO_DAYS,
    add_executor_deployed_pair,
    clear_preferences,
    get_dex_prefs,
    get_executor_prefs,
    get_gateway_prefs,
    get_portfolio_prefs,
    get_preferences,
    set_dex_last_pool,
    set_executor_last_config,
    set_portfolio_days,
    set_wallet_networks,
)

USER_ID = 4242
WALLET = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"


@pytest.fixture
def cm(tmp_path, monkeypatch):
    """A real ConfigManager on a throwaway config.yml — persistence included."""
    monkeypatch.chdir(tmp_path)  # audit_log.yml is written relative to cwd
    manager = cm_module.ConfigManager(config_path=str(tmp_path / "config.yml"))
    manager._data["user_preferences"] = {}
    manager._audit_log = []
    manager._save_config()
    monkeypatch.setattr(cm_module, "get_config_manager", lambda: manager)
    return manager


def test_previously_unsynced_setters_reach_config(cm):
    """The four setters that silently skipped the sync now persist."""
    user_data = {"_user_id": USER_ID}

    set_wallet_networks(user_data, WALLET, ["solana-mainnet-beta"])
    set_dex_last_pool(user_data, {"trading_pair": "SOL-USDC"})
    add_executor_deployed_pair(user_data, "SOL-USDC")
    set_executor_last_config(user_data, "grid", {"total_amount_quote": 100})

    stored = cm.get_user_preferences(USER_ID)
    assert stored["gateway"]["wallet_networks"] == {WALLET: ["solana-mainnet-beta"]}
    assert stored["dex"]["last_pool"] == {"trading_pair": "SOL-USDC"}
    assert stored["executors"]["deployed_pairs"] == ["SOL-USDC"]
    assert stored["executors"]["last_grid"] == {"total_amount_quote": 100}


def test_wallets_survive_a_reset_of_the_pickle(cm):
    """What the scrubber reads: wallets written in Telegram, read config-only."""
    set_wallet_networks({"_user_id": USER_ID}, WALLET, ["solana-mainnet-beta"])

    fresh = prefs_module.load_user_data_for(USER_ID)

    assert list(get_gateway_prefs(fresh).get("wallet_networks")) == [WALLET]


def test_accessor_hydrates_from_config_without_get_preferences(cm):
    """A cold pickle reads config.yml through any accessor, not just two."""
    cm.set_user_preference(USER_ID, "dex", {"default_slippage": "3.5"})

    user_data = {"_user_id": USER_ID}
    assert get_dex_prefs(user_data).get("default_slippage") == "3.5"

    cm.set_user_preference(USER_ID, "executors", {"deployed_pairs": ["ETH-USDC"]})
    assert get_executor_prefs({"_user_id": USER_ID})["deployed_pairs"] == ["ETH-USDC"]


def test_clear_preferences_clears_the_config_copy(cm):
    """Otherwise the next hydration restores exactly what was cleared."""
    user_data = {"_user_id": USER_ID}
    set_portfolio_days(user_data, 30)
    set_wallet_networks(user_data, WALLET, ["base"])
    assert cm.get_user_preferences(USER_ID)["portfolio"]["days"] == 30

    clear_preferences(user_data)

    assert get_portfolio_prefs(user_data)["days"] == DEFAULT_PORTFOLIO_DAYS
    assert get_gateway_prefs(user_data)["wallet_networks"] == {}
    assert get_preferences({"_user_id": USER_ID})["portfolio"]["days"] == (
        DEFAULT_PORTFOLIO_DAYS
    )
    assert cm.get_user_preferences(USER_ID) == {}


def test_clear_preferences_keeps_reserved_keys(cm):
    """A preference reset must not revoke the code_run capability grant."""
    cm.set_user_preference(USER_ID, cm_module.CODE_RUN_PREFERENCE, True)
    set_portfolio_days({"_user_id": USER_ID}, 30)

    clear_preferences({"_user_id": USER_ID})

    stored = cm.get_user_preferences(USER_ID)
    assert stored == {cm_module.CODE_RUN_PREFERENCE: True}


def test_every_mutator_routes_through_mutate():
    """The sync is structural: a setter cannot forget what it never calls."""
    mutators = [
        name
        for name in dir(prefs_module)
        if name.startswith(("set_", "add_", "remove_"))
        and inspect.isfunction(getattr(prefs_module, name))
        and getattr(prefs_module, name).__module__ == prefs_module.__name__
    ]
    assert mutators, "no mutators found — the check would pass vacuously"

    missing = [
        name
        for name in mutators
        if "_mutate(" not in inspect.getsource(getattr(prefs_module, name))
    ]
    assert missing == []

    # ...and there is exactly one place left that syncs a section.
    source = inspect.getsource(prefs_module)
    assert source.count("_sync_section_to_cm(user_data, section)") == 1
