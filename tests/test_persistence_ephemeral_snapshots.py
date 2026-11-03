"""PERF-241: transient API snapshots never reach the pickle on disk.

``SafePicklePersistence`` re-serializes the whole store and fsyncs it —
synchronously, on the bot's event loop — every time PTB flushes a touched
user. ``EPHEMERAL_KEYS`` existed to keep rebuildable data out of that write,
but it only covered the cache namespaces and the ``portfolio_*`` snapshots.
The per-render API snapshots the bots/executors/LP handlers stash in
``user_data`` (an ``active_bots_data`` alone measured 108 KB in the live file)
were pickled and fsynced along with them, despite every read site re-fetching
on a miss. These pin that they are stripped from the on-disk copy while the
running session keeps them in memory.
"""

from __future__ import annotations

import pickle

from condor.persistence import EPHEMERAL_KEYS, SafePicklePersistence

# The per-render snapshots added by PERF-241 — each written by a handler and
# re-fetched from the backend by the next render that needs it.
TRANSIENT_SNAPSHOT_KEYS = (
    "active_bots_data",
    "current_bot_info",
    "bot_runs_map",
    "controller_configs_list",
    "configs_type_filtered",
    "current_executor",
    "running_executors",
    "gs_candles",
    "selected_pool_info",
    "gecko_pools",
    "pool_list_cache",
    "positions_cache",
    "lp_positions_cache",
)

# What the user actually loses on a restart if we strip too much.
DURABLE_KEYS = {
    "user_preferences": {"default_server": "prod", "quote": "USDC"},
    "clob_last_order": {"pair": "SOL-USDC", "amount": "1"},
    "selected_server": "prod",
}


def _user_data_with_snapshots(uid: int = 42) -> dict:
    data = dict(DURABLE_KEYS)
    for key in TRANSIENT_SNAPSHOT_KEYS:
        data[key] = {"snapshot": [key] * 50}
    return {uid: data}


def test_every_transient_snapshot_key_is_ephemeral():
    """The 13 snapshot keys are registered, so no read site is left guessing."""
    assert set(TRANSIENT_SNAPSHOT_KEYS) <= EPHEMERAL_KEYS


def test_strip_drops_snapshots_and_keeps_durable_state():
    cleaned = SafePicklePersistence._strip_ephemeral(_user_data_with_snapshots())

    written = cleaned[42]
    for key in TRANSIENT_SNAPSHOT_KEYS:
        assert key not in written, f"{key} was persisted"
    assert written == DURABLE_KEYS


def test_strip_does_not_mutate_the_live_session_data():
    """Handlers keep reading their snapshots from memory after a flush."""
    user_data = _user_data_with_snapshots()
    live = user_data[42]
    before = dict(live)

    SafePicklePersistence._strip_ephemeral(user_data)

    assert live == before
    for key in TRANSIENT_SNAPSHOT_KEYS:
        assert key in live


def test_pickle_shrinks_by_an_order_of_magnitude():
    """The whole point: the bytes that get fsynced on the loop."""
    # Sized like the live file, where active_bots_data alone was ~108 KB
    # against ~1 KB of user_preferences.
    user_data = {
        7: {
            **DURABLE_KEYS,
            "active_bots_data": {
                f"bot-{i}": {"status": "running"} for i in range(2000)
            },
            "current_executor": {f"field-{i}": i for i in range(2000)},
        }
    }

    full = len(pickle.dumps(user_data, protocol=pickle.HIGHEST_PROTOCOL))
    stripped = len(
        pickle.dumps(
            SafePicklePersistence._strip_ephemeral(user_data),
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    )

    assert stripped * 10 < full


def test_users_without_snapshots_are_passed_through_untouched():
    """No needless copy for the common idle user."""
    user_data = {1: dict(DURABLE_KEYS), 2: "not-a-dict"}

    cleaned = SafePicklePersistence._strip_ephemeral(user_data)

    assert cleaned[1] is user_data[1]
    assert cleaned[2] == "not-a-dict"
