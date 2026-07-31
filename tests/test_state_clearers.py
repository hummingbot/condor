"""Tests for CORR-028: narrow state-clear helpers must not wipe ALL state.

The narrow cleaners (``clear_executors_state``, ``clear_bots_state``,
``clear_archived_state``) are invoked mid/end of bots/executors/archived flows
and on menu re-entry. They must pop ONLY their own feature keys and must NOT
tear down unrelated state — in particular an active /trade view's SDS
subscriptions (``tg_trade_{user_id}``) or the portfolio cache. Only the master
``clear_all_input_states`` (used by top-level command entrypoints) does that.
"""

from types import SimpleNamespace

from condor.server_data_service import (
    ServerDataType,
    Subscription,
    get_server_data_service,
)
from handlers import clear_all_input_states
from handlers.bots._shared import clear_bots_state
from handlers.bots.archived import clear_archived_state
from handlers.executors._shared import clear_executors_state


def _ctx(user_id="42", **extra):
    user_data = {"_user_id": user_id}
    user_data.update(extra)
    return SimpleNamespace(user_data=user_data)


def _register_trade_subscription(user_id):
    """Register a fake active /trade SDS subscription for the user."""
    from condor.server_data_service import CacheKey

    sds = get_server_data_service()
    subscriber_id = f"tg_trade_{user_id}"
    key = CacheKey.make("srv", ServerDataType.PRICES, trading_pair="BTC-USDT")
    sds._subscriptions.setdefault(key, {})[subscriber_id] = Subscription(
        subscriber_id=subscriber_id, key=key, interval=1.0, callback=None
    )
    return sds, subscriber_id


def _has_trade_subscription(sds, subscriber_id):
    return any(subscriber_id in subs for subs in sds._subscriptions.values())


def test_narrow_clears_preserve_trade_subscriptions_and_portfolio_cache():
    """Narrow cleaners pop their own keys but leave SDS + portfolio cache intact."""
    user_id = "corr028_narrow"

    for clearer, own_key in (
        (clear_executors_state, "executors_state"),
        (clear_bots_state, "bots_state"),
        (clear_archived_state, "archived_databases"),
    ):
        sds, subscriber_id = _register_trade_subscription(user_id)
        ctx = _ctx(
            user_id=user_id,
            **{own_key: "something", "portfolio_balances": {"BTC": 1}},
        )

        clearer(ctx)

        # Own feature key is cleared
        assert (
            own_key not in ctx.user_data
        ), f"{clearer.__name__} should clear {own_key}"
        # Unrelated state is preserved
        assert ctx.user_data.get("portfolio_balances") == {
            "BTC": 1
        }, f"{clearer.__name__} must not drop portfolio cache"
        assert _has_trade_subscription(
            sds, subscriber_id
        ), f"{clearer.__name__} must not tear down active trade subscriptions"

        # Cleanup
        sds.unsubscribe_all(subscriber_id)


def test_master_clear_tears_down_trade_subscriptions():
    """The master cleaner still fully resets state, including SDS teardown."""
    user_id = "corr028_master"
    sds, subscriber_id = _register_trade_subscription(user_id)
    ctx = _ctx(user_id=user_id, bots_state="x", portfolio_balances={"BTC": 1})

    clear_all_input_states(ctx)

    assert "bots_state" not in ctx.user_data
    assert "portfolio_balances" not in ctx.user_data
    assert not _has_trade_subscription(
        sds, subscriber_id
    ), "clear_all_input_states must tear down trade subscriptions"
