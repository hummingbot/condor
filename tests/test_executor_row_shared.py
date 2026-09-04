"""Tests for ARCH-119: one executor display-row transform, shared by every layer.

The raw-executor -> display-row transform used to be written twice, once in the
web route and once in the agents rollup, with no shared source. The two copies
had already diverged on ``current_price`` and neither was a superset, so the same
executor rendered a live price in the executors tab and ``0`` in the agent's
session view -- both of which feed the *same* React table. These tests pin the
union rule, the per-caller conventions that legitimately differ, and the fact
that no second copy of the fallback chains has grown back.
"""

import pathlib

import pytest

from condor.agents.performance import _executor_row
from condor.fetchers.executors import build_executor_row
from condor.web.models import ExecutorInfo

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── The divergence this item closes ──


def test_custom_info_current_price_reaches_both_layers():
    """The agents copy knew ``custom_info.current_price``; the web copy did not."""
    ex = {"id": "e1", "custom_info": {"current_price": 101.5}}

    assert _executor_row(ex)["current_price"] == 101.5
    assert ExecutorInfo.from_raw(ex).current_price == 101.5


def test_held_position_order_price_reaches_both_layers():
    """The web copy knew ``held_position_orders``; the agents copy did not."""
    ex = {
        "id": "e1",
        "custom_info": {"held_position_orders": [{"price": 7.25}]},
    }

    assert _executor_row(ex)["current_price"] == 7.25
    assert ExecutorInfo.from_raw(ex).current_price == 7.25


def test_current_price_precedence_is_the_union_of_both_chains():
    """Top-level wins, then custom_info.current_price, close_price, held order."""
    ci = {
        "current_price": 2.0,
        "close_price": 3.0,
        "held_position_orders": [{"price": 4.0}],
    }
    assert (
        build_executor_row({"current_price": 1.0, "custom_info": ci})["current_price"]
        == 1.0
    )
    assert build_executor_row({"custom_info": ci})["current_price"] == 2.0
    assert (
        build_executor_row(
            {"custom_info": {k: v for k, v in ci.items() if k != "current_price"}}
        )["current_price"]
        == 3.0
    )
    assert (
        build_executor_row({"custom_info": {"held_position_orders": [{"price": 4.0}]}})[
            "current_price"
        ]
        == 4.0
    )


@pytest.mark.parametrize(
    "custom_info,expected",
    [
        ({"current_position_average_price": 12.0}, 12.0),
        ({"break_even_price": 9.0}, 9.0),
        ({"current_position_average_price": 12.0, "break_even_price": 9.0}, 12.0),
    ],
)
def test_entry_price_chain_is_shared(custom_info, expected):
    ex = {"id": "e1", "custom_info": custom_info}

    assert _executor_row(ex)["entry_price"] == expected
    assert ExecutorInfo.from_raw(ex).entry_price == expected


def test_config_entry_price_outranks_custom_info():
    ex = {"config": {"entry_price": 50.0}, "custom_info": {"break_even_price": 9.0}}

    assert build_executor_row(ex)["entry_price"] == 50.0


# ── Conventions that must stay per-caller ──


def test_each_layer_keeps_its_own_wire_shape():
    """Sharing the transform must not rename the fields either wire already uses."""
    ex = {
        "id": "e1",
        "status": "Running",
        "close_type": "TAKE_PROFIT",
        "config": {"type": "PositionExecutor", "trading_pair": "BTC-USDT"},
        "cum_fees_quote": 1.5,
    }

    row = _executor_row(ex)
    info = ExecutorInfo.from_raw(ex)

    # The agents wire: raw type, uppercase status, ``pair`` / ``fees``.
    assert row["type"] == "PositionExecutor"
    assert row["status"] == "RUNNING"
    assert row["pair"] == "BTC-USDT"
    assert row["fees"] == 1.5
    # The REST/WS wire: normalized type label, lowercase status/close_type.
    assert info.type == "position"
    assert info.status == "running"
    assert info.close_type == "take_profit"
    assert info.trading_pair == "BTC-USDT"
    assert info.cum_fees_quote == 1.5


def test_agent_row_keeps_its_amount_field():
    row = _executor_row({"config": {"total_amount_quote": 250.0}})

    assert row["amount"] == 250.0


def test_non_dict_executor_has_no_info_row():
    assert ExecutorInfo.from_raw("not-an-executor") is None
    assert ExecutorInfo.from_raw(None) is None


def test_non_dict_custom_info_does_not_break_the_row():
    """A non-dict ``custom_info`` used to reach the web copy unguarded and raise."""
    info = ExecutorInfo.from_raw({"id": "e1", "custom_info": [1, 2]})

    assert info is not None
    assert info.custom_info == {}


def test_connector_from_config_is_not_dropped():
    """The web copy read only the top-level ``connector`` and rendered a blank."""
    ex = {"id": "e1", "config": {"type": "grid_executor", "connector": "kucoin"}}

    assert _executor_row(ex)["connector"] == "kucoin"
    assert ExecutorInfo.from_raw(ex).connector == "kucoin"


# ── No second copy ──


MARKERS = ("current_position_average_price", "break_even_price", "held_position_orders")

# ``condor/fetchers/archived_run.py`` reads ``current_position_average_price`` too,
# but it is not a copy of this transform: it maps *archived* executors out of a DB
# export, where the row's price is by definition the close price and the
# timestamps arrive as ISO strings. It is listed here so a real second copy of the
# live transform still trips the assertion below.
KNOWN_SEPARATE_TRANSFORMS = {"condor/fetchers/archived_run.py"}


@pytest.mark.parametrize("marker", MARKERS)
def test_live_fallback_chain_is_written_exactly_once(marker):
    """Every live-executor price fallback lives in build_executor_row, once."""
    offenders = {
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "condor").rglob("*.py")
        if marker in path.read_text()
    } - KNOWN_SEPARATE_TRANSFORMS

    assert offenders == {"condor/fetchers/executors.py"}


@pytest.mark.parametrize(
    "module",
    [
        "condor/web/routes/executors.py",
        "condor/web/ws_manager.py",
        "condor/agents/performance.py",
    ],
)
def test_consolidated_callers_reimplement_no_chain(module):
    """The three layers ARCH-119 unified delegate; none rebuilds a price chain."""
    source = (REPO_ROOT / module).read_text()

    assert [m for m in MARKERS if m in source] == []


def test_ws_broadcast_does_not_reach_into_the_route_module():
    source = (REPO_ROOT / "condor" / "web" / "ws_manager.py").read_text()

    assert "_build_executor_info" not in source
