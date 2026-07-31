"""Golden tests for the shared bot-status transform (ARCH-050).

The expected dicts below were captured from the pre-refactor implementations
(the inline transform in ``list_bots`` and ``ws_manager._transform_bots``)
running on the same sample payload, so these tests pin byte-identical
behavior for both the REST and WS paths.
"""

from condor.fetchers.bots import build_bots_page, extract_bots_list
from condor.web.models import BotsPageResponse

SAMPLE_RAW = {
    "status": "success",
    "data": {
        "epsilon": {
            "status": "running",
            "performance": {
                "pmm_binance_BTC-USDT_1": {
                    "status": "running",
                    "performance": {
                        "realized_pnl_quote": 1.5,
                        "unrealized_pnl_quote": -0.5,
                        "global_pnl_pct": 0.12,
                        "volume_traded": 1234.5,
                        "close_type_counts": {"TAKE_PROFIT": 3},
                        "positions_summary": [{"pair": "BTC-USDT"}],
                    },
                },
                "bad_ctrl": "not-a-dict",
                "quiet_ctrl": {
                    "status": "stopped",
                    "performance": {},
                },
            },
            "error_logs": [{"msg": "e1"}],
            "general_logs": [{"msg": "g1"}],
        },
        "zeta": {
            "status": "stopped",
            "performance": "not-a-dict",
            "error_logs": "bad",
        },
    },
}

CTRL_CONFIGS = {
    "pmm_binance_BTC-USDT_1": {
        "id": "cfg-123",
        "controller_name": "pmm_simple",
        "connector_name": "binance",
        "trading_pair": "BTC-USDT",
        "manual_kill_switch": False,
    },
}

BOT_RUNS = {"epsilon": "2026-07-01T00:00:00Z"}

LATEST_PERF = {
    "cfg-123": {
        "performance": {"realized_pnl_quote": 9.9, "volume_traded": 777},
    },
    "quiet_ctrl": {
        "performance": {
            "realized_pnl_quote": 2.0,
            "unrealized_pnl_quote": 0.25,
            "global_pnl_pct": 0.05,
            "volume_traded": 50.0,
            "connector": "kucoin",
            "trading_pair": "ETH-USDT",
        },
    },
}

# Captured from the pre-refactor inline transform in list_bots
# (condor/web/routes/bots.py) with the enrichment maps above.
GOLDEN_REST = {
    "controllers": [
        {
            "controller_name": "pmm_simple",
            "controller_id": "cfg-123",
            "bot_name": "epsilon",
            "status": "running",
            "connector": "binance",
            "trading_pair": "BTC-USDT",
            "realized_pnl_quote": 1.5,
            "unrealized_pnl_quote": -0.5,
            "global_pnl_quote": 1.0,
            "global_pnl_pct": 0.12,
            "volume_traded": 1234.5,
            "close_type_counts": {"TAKE_PROFIT": 3},
            "positions_summary": [{"pair": "BTC-USDT"}],
            "deployed_at": "2026-07-01T00:00:00Z",
            "config": {
                "id": "cfg-123",
                "controller_name": "pmm_simple",
                "connector_name": "binance",
                "trading_pair": "BTC-USDT",
                "manual_kill_switch": False,
            },
        },
        {
            "controller_name": "quiet_ctrl",
            "controller_id": "quiet_ctrl",
            "bot_name": "epsilon",
            "status": "stopped",
            "connector": "kucoin",
            "trading_pair": "ETH-USDT",
            "realized_pnl_quote": 2.0,
            "unrealized_pnl_quote": 0.25,
            "global_pnl_quote": 2.25,
            "global_pnl_pct": 0.05,
            "volume_traded": 50.0,
            "close_type_counts": {},
            "positions_summary": [],
            "deployed_at": "2026-07-01T00:00:00Z",
            "config": {},
        },
    ],
    "bots": [
        {
            "bot_name": "epsilon",
            "status": "running",
            "num_controllers": 2,
            "error_count": 1,
            "deployed_at": "2026-07-01T00:00:00Z",
            "error_logs": [{"msg": "e1"}],
            "general_logs": [{"msg": "g1"}],
        },
        {
            "bot_name": "zeta",
            "status": "stopped",
            "num_controllers": 0,
            "error_count": 0,
            "deployed_at": None,
            "error_logs": [],
            "general_logs": [],
        },
    ],
    "total_pnl": 3.25,
    "total_volume": 1284.5,
    "server_online": True,
    "error_hint": None,
}

# Captured from the pre-refactor ws_manager._transform_bots (no enrichment).
GOLDEN_WS = {
    "controllers": [
        {
            "controller_name": "pmm_binance_BTC-USDT_1",
            "controller_id": "pmm_binance_BTC-USDT_1",
            "bot_name": "epsilon",
            "status": "running",
            "connector": "pmm_binance",
            "trading_pair": "BTC-USDT",
            "realized_pnl_quote": 1.5,
            "unrealized_pnl_quote": -0.5,
            "global_pnl_quote": 1.0,
            "global_pnl_pct": 0.12,
            "volume_traded": 1234.5,
            "close_type_counts": {"TAKE_PROFIT": 3},
            "positions_summary": [{"pair": "BTC-USDT"}],
            "deployed_at": None,
            "config": {},
        },
        {
            "controller_name": "quiet_ctrl",
            "controller_id": "quiet_ctrl",
            "bot_name": "epsilon",
            "status": "stopped",
            "connector": "",
            "trading_pair": "",
            "realized_pnl_quote": 0.0,
            "unrealized_pnl_quote": 0.0,
            "global_pnl_quote": 0.0,
            "global_pnl_pct": 0.0,
            "volume_traded": 0.0,
            "close_type_counts": {},
            "positions_summary": [],
            "deployed_at": None,
            "config": {},
        },
    ],
    "bots": [
        {
            "bot_name": "epsilon",
            "status": "running",
            "num_controllers": 2,
            "error_count": 1,
            "deployed_at": None,
            "error_logs": [{"msg": "e1"}],
            "general_logs": [{"msg": "g1"}],
        },
        {
            "bot_name": "zeta",
            "status": "stopped",
            "num_controllers": 0,
            "error_count": 0,
            "deployed_at": None,
            "error_logs": [],
            "general_logs": [],
        },
    ],
    "total_pnl": 1.0,
    "total_volume": 1234.5,
    "server_online": True,
}


def test_rest_response_matches_pre_refactor_golden():
    """build_bots_page with enrichment reproduces the old list_bots response."""
    page = build_bots_page(
        SAMPLE_RAW,
        ctrl_configs=CTRL_CONFIGS,
        bot_runs=BOT_RUNS,
        latest_perf=LATEST_PERF,
    )
    assert BotsPageResponse(**page).model_dump() == GOLDEN_REST


def test_ws_transform_matches_pre_refactor_golden():
    """build_bots_page without enrichment reproduces the old _transform_bots."""
    assert build_bots_page(SAMPLE_RAW) == GOLDEN_WS


def test_ws_manager_delegates_to_shared_transform():
    from condor.web.ws_manager import WebSocketManager

    assert WebSocketManager._transform_bots(SAMPLE_RAW) == build_bots_page(SAMPLE_RAW)


def test_extract_bots_list_handles_malformed_inputs():
    assert extract_bots_list(None) == []
    assert extract_bots_list("<html>error</html>") == []
    assert extract_bots_list({"status": "error", "message": "boom"}) == []
    assert extract_bots_list({"data": [{"bot_name": "a"}, "junk"]}) == [
        {"bot_name": "a"}
    ]
    assert extract_bots_list([{"bot_name": "b"}, 42]) == [{"bot_name": "b"}]
