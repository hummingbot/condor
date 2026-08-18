"""Tests for CORR-111: one canonical side normalizer, used by every path.

The API reports an executor/position side as an enum int (``1``/``2``), a bare
word (``BUY``/``SELL``/``LONG``/``SHORT``) or a stringified enum
(``TradeType.SELL``). Four partial normalizers used to exist and the agents path
had none, so a literal ``1`` or ``TradeType.SELL`` reached the UI — which is why
``ExecutorTable`` carried a ``side === "1"`` fallback. These tests pin the union
rule and the fact that no private variant has grown back.
"""

import pathlib

import pytest

from condor.agents.performance import _executor_row
from condor.backtesting import normalize_backtest_task
from condor.fetchers.executors import normalize_executor_side

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "raw",
    [
        1,
        "1",
        "BUY",
        "buy",
        " buy ",
        "LONG",
        "long",
        "TradeType.BUY",
        "PositionSide.LONG",
    ],
)
def test_every_buy_encoding_normalizes_to_buy(raw):
    assert normalize_executor_side(raw) == "BUY"


@pytest.mark.parametrize(
    "raw",
    [2, "2", "SELL", "sell", "SHORT", "short", "TradeType.SELL", "PositionSide.SHORT"],
)
def test_every_sell_encoding_normalizes_to_sell(raw):
    assert normalize_executor_side(raw) == "SELL"


@pytest.mark.parametrize("raw", [None, "", 0, "   "])
def test_missing_side_is_empty(raw):
    assert normalize_executor_side(raw) == ""


def test_unknown_side_passes_through_uppercased():
    """Never coerce an unseen value: an unknown side must stay visible, not read as a buy."""
    assert normalize_executor_side("neutral") == "NEUTRAL"


def test_numeric_side_is_not_split_on_its_decimal_point():
    """The dotted-prefix strip must not turn ``1.0`` into ``0``."""
    assert normalize_executor_side(1.0) == "1.0"


def test_agent_row_normalizes_a_raw_integer_side():
    """The agents path used to emit ``str(side)`` verbatim, printing a literal ``1``."""
    row = _executor_row({"id": "e1", "config": {"side": 1}})

    assert row["side"] == "BUY"


def test_agent_row_reads_custom_info_side_first():
    """Position executors carry the side in ``custom_info``, as the web route already knew."""
    row = _executor_row(
        {"id": "e1", "config": {"side": 1}, "custom_info": {"side": "TradeType.SELL"}}
    )

    assert row["side"] == "SELL"


def test_no_private_side_normalizer_survives():
    """All four call sites share the canonical helper; none re-grew a local variant."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "condor").rglob("*.py")
        if "def _normalize_side" in path.read_text()
        or "def _clean_side" in path.read_text()
    ]

    assert offenders == []


# ── ARCH-121: the backtest wire carries the same normalized side ──


def test_backtest_task_sides_are_normalized_in_the_envelope():
    """The dashboard reads ``task["result"]["executors"][i]["side"]`` verbatim now."""
    task = {
        "task_id": "t1",
        "status": "completed",
        "result": {
            "executors": [
                {"id": "e1", "side": "TradeType.BUY"},
                {"id": "e2", "side": 2},
                {"id": "e3", "side": "SHORT"},
                {"id": "e4", "side": "1"},
            ]
        },
    }

    normalize_backtest_task(task)

    assert [e["side"] for e in task["result"]["executors"]] == [
        "BUY",
        "SELL",
        "SELL",
        "BUY",
    ]


def test_backtest_payload_without_an_envelope_is_normalized_too():
    """A bare result payload — what a caller holding only ``result`` passes in."""
    payload = {"executors": [{"id": "e1", "side": "sell"}]}

    assert normalize_backtest_task(payload)["executors"][0]["side"] == "SELL"


def test_backtest_normalization_adds_no_side_key():
    """The raw payload is rendered as JSON in the dashboard: do not invent fields."""
    task = {"result": {"executors": [{"id": "e1"}]}}

    normalize_backtest_task(task)

    assert task["result"]["executors"] == [{"id": "e1"}]


@pytest.mark.parametrize("task", [None, "nope", {}, {"result": {"executors": "nope"}}])
def test_backtest_normalization_tolerates_a_payload_without_executors(task):
    """A pending/failed task has no executors list; the route still returns it."""
    assert normalize_backtest_task(task) == task


def test_no_typescript_copy_of_the_side_rule_survives():
    """ARCH-121: ``normalizeSide`` was a second definition of the rule, in TypeScript.

    Deleted once the backtest route started emitting a normalized side. If it comes
    back, the rule has two definitions in two languages again, free to drift.
    """
    src = REPO_ROOT / "frontend" / "src"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in src.rglob("*.ts*")
        if "function normalizeSide" in path.read_text()
    ]

    assert offenders == []
