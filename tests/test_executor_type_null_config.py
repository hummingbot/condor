"""Tests for CORR-131: a null ``config`` must not take down the executor listing.

``get_executor_type`` resolved the config with ``executor.get("config", executor)``,
whose default only fires when the key is *absent*. The backend does emit rows with
the key present and explicitly ``null``, which made ``config`` ``None`` and raised
``AttributeError`` on the first ``source.get(...)``. Since the helper runs while
building every display row (REST listing, WS broadcast, agents rollup), one
malformed executor 502'd the whole listing instead of degrading that single row.
"""

from condor.fetchers.executors import build_executor_row, get_executor_type
from condor.web.models import ExecutorInfo
from condor.web.routes.executors import _offset_page

# ── The crash this item closes ──


def test_null_config_returns_a_label_instead_of_raising():
    assert get_executor_type({"config": None}) == "unknown"


def test_non_dict_config_returns_a_label_instead_of_raising():
    assert get_executor_type({"config": "not-a-dict"}) == "unknown"
    assert get_executor_type({"config": []}) == "unknown"


def test_null_config_still_resolves_a_top_level_type():
    """The fallback to the executor itself survives the null-safety guard."""
    assert get_executor_type({"config": None, "type": "PositionExecutor"}) == "position"
    assert (
        get_executor_type({"config": None, "executor_type": "GridExecutor"}) == "grid"
    )


# ── The blast radius: one bad row must not sink the page ──


def test_null_config_row_survives_the_display_row_transform():
    row = build_executor_row({"id": "e1", "config": None})

    assert row["id"] == "e1"
    assert row["config"] == {}
    assert ExecutorInfo.from_raw({"id": "e1", "config": None}).type == "unknown"


def test_one_null_config_row_does_not_break_the_rest_of_the_listing():
    """The 502: the listing loop raised on the bad row before reaching the good ones."""
    rows = [
        {"id": "good-1", "config": {"type": "PositionExecutor"}},
        {"id": "bad", "config": None},
        {"id": "good-2", "config": {"type": "GridExecutor"}},
    ]

    items = _offset_page(rows, 0, 10)["executors"]

    assert [i.id for i in items] == ["good-1", "bad", "good-2"]
    assert [i.type for i in items] == ["position", "unknown", "grid"]


# ── Well-formed executors resolve exactly as before ──


def test_type_resolution_is_unchanged_for_well_formed_executors():
    assert get_executor_type({"config": {"type": "PositionExecutor"}}) == "position"
    assert get_executor_type({"config": {"executor_type": "DCAExecutor"}}) == "dca"
    assert get_executor_type({"type": "OrderExecutor"}) == "order"
    # Shape inference, which reads the executor itself when there is no config key.
    assert get_executor_type({"start_price": 1, "end_price": 2}) == "grid"
    assert get_executor_type({"config": {"stop_loss": 0.01}}) == "position"
    assert get_executor_type({"trailing_stop": {}}) == "position"
    assert get_executor_type({}) == "unknown"
