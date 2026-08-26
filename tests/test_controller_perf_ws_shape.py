"""The ``controller_perf`` WS frames must carry REST-shaped snapshots (CORR-224).

The dashboard merges the socket's snapshots into the very same react-query
cache that ``/controller-performance/history`` fills, so the two paths have to
agree field for field. The stream used to broadcast the upstream payload
unflattened -- with the numbers still nested under ``performance`` -- which
would have appended points reading ``undefined`` for every PnL and volume once
the frames actually reached the cache.
"""

from condor.web.models import ControllerPerformanceSnapshot
from condor.web.streams.hummingbot_ws import HummingbotStreamsMixin

RAW_NESTED = {
    "timestamp": 1735689600,
    "bot_name": "epsilon",
    "controller_id": "pmm_1",
    "controller_name": "pmm_simple",
    "connector_name": "binance",
    "trading_pair": "BTC-USDT",
    "performance": {
        "realized_pnl_quote": 1.5,
        "unrealized_pnl_quote": -0.5,
        "global_pnl_quote": 1.0,
        "global_pnl_pct": 0.12,
        "volume_traded": 1234.5,
        "close_type_counts": {"TAKE_PROFIT": 3},
        "positions_summary": [{"pair": "BTC-USDT"}],
    },
}


def test_from_raw_flattens_nested_performance():
    snap = ControllerPerformanceSnapshot.from_raw(RAW_NESTED)

    assert snap.timestamp == "1735689600"
    assert snap.bot_name == "epsilon"
    assert snap.controller_id == "pmm_1"
    # `connector_name` is the upstream spelling; the wire model exposes `connector`
    assert snap.connector == "binance"
    assert snap.realized_pnl_quote == 1.5
    assert snap.global_pnl_quote == 1.0
    assert snap.volume_traded == 1234.5
    assert snap.close_type_counts == {"TAKE_PROFIT": 3}
    assert snap.positions_summary == [{"pair": "BTC-USDT"}]


def test_from_raw_accepts_already_flat_payload():
    snap = ControllerPerformanceSnapshot.from_raw(
        {"controller_id": "c1", "global_pnl_quote": 7.25, "volume_traded": 10}
    )

    assert snap.controller_id == "c1"
    assert snap.global_pnl_quote == 7.25
    assert snap.volume_traded == 10.0


def test_ws_transform_matches_the_rest_snapshot_shape():
    """A broadcast frame is byte-identical to what the REST route would return."""
    result = {"data": [RAW_NESTED]}

    broadcast = HummingbotStreamsMixin._transform_controller_perf(result)

    assert broadcast == [
        ControllerPerformanceSnapshot.from_raw(RAW_NESTED).model_dump()
    ]
    assert broadcast[0]["global_pnl_quote"] == 1.0
    # The nested container must not survive into the frame
    assert "performance" not in broadcast[0]


def test_ws_transform_keys_controller_id_from_a_dict_payload():
    """A dict keyed by controller_id still yields identified snapshots."""
    broadcast = HummingbotStreamsMixin._transform_controller_perf(
        {
            "data": {
                "pmm_1": {"bot_name": "epsilon", "performance": {"volume_traded": 5}}
            }
        }
    )

    assert len(broadcast) == 1
    assert broadcast[0]["controller_id"] == "pmm_1"
    assert broadcast[0]["volume_traded"] == 5.0


def test_ws_transform_tolerates_junk():
    assert HummingbotStreamsMixin._transform_controller_perf(None) == []
    assert HummingbotStreamsMixin._transform_controller_perf({"data": "nope"}) == []
