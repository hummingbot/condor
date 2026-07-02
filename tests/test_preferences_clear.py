"""Tests for preference getters after clear_preferences (CORR-068).

clear_preferences used to delete user_preferences while leaving the
_prefs_migration_done flag set, so _migrate_legacy_data early-returned
without rebuilding the structure and every raw-indexing getter raised
KeyError. Getters must return defaults instead.
"""

import pytest

from condor.preferences import (
    _MIGRATION_DONE_KEY,
    DEFAULT_CLOB_ACCOUNT,
    DEFAULT_PORTFOLIO_DAYS,
    DEFAULT_PORTFOLIO_INTERVAL,
    clear_preferences,
    get_agent_prefs,
    get_clob_prefs,
    get_dex_prefs,
    get_executor_prefs,
    get_gateway_prefs,
    get_general_prefs,
    get_notes,
    get_portfolio_prefs,
    get_preferences,
    get_unified_trade_prefs,
    get_voice_prefs,
    set_note,
    set_portfolio_days,
)

PUBLIC_GETTERS = [
    get_preferences,
    get_portfolio_prefs,
    get_clob_prefs,
    get_dex_prefs,
    get_general_prefs,
    get_gateway_prefs,
    get_unified_trade_prefs,
    get_executor_prefs,
    get_agent_prefs,
    get_voice_prefs,
    get_notes,
]


def _migrated_user_data() -> dict:
    """user_data that has already gone through migration (flag set)."""
    user_data = {}
    get_preferences(user_data)  # builds structure and sets the migration flag
    assert user_data.get(_MIGRATION_DONE_KEY) is True
    return user_data


@pytest.mark.parametrize("getter", PUBLIC_GETTERS, ids=lambda fn: fn.__name__)
def test_getter_after_clear_returns_defaults(getter):
    user_data = _migrated_user_data()
    clear_preferences(user_data)

    result = getter(user_data)  # must not raise KeyError

    assert isinstance(result, dict)


def test_clear_then_getters_return_default_values():
    user_data = _migrated_user_data()
    set_portfolio_days(user_data, 30)
    set_note(user_data, "alpha", "beta")

    clear_preferences(user_data)

    portfolio = get_portfolio_prefs(user_data)
    assert portfolio["days"] == DEFAULT_PORTFOLIO_DAYS
    assert portfolio["interval"] == DEFAULT_PORTFOLIO_INTERVAL
    assert get_clob_prefs(user_data)["account"] == DEFAULT_CLOB_ACCOUNT
    assert get_notes(user_data) == {}


def test_clear_preferences_resets_bookkeeping_flags():
    user_data = _migrated_user_data()
    user_data["_prefs_hydrated"] = True

    clear_preferences(user_data)

    assert _MIGRATION_DONE_KEY not in user_data
    assert "_prefs_hydrated" not in user_data


def test_existing_preferences_unchanged_by_getters():
    """No behavior change for users with existing preferences."""
    user_data = _migrated_user_data()
    set_portfolio_days(user_data, 14)
    set_note(user_data, "k", "v")

    assert get_portfolio_prefs(user_data)["days"] == 14
    assert get_notes(user_data) == {"k": "v"}
    # Migration flag stays set; repeated getter calls keep values intact
    assert user_data.get(_MIGRATION_DONE_KEY) is True
    assert get_portfolio_prefs(user_data)["days"] == 14
