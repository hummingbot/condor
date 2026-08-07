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
