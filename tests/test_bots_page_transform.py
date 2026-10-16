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
            "controller_type": "",
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
            "controller_name": "",
            "controller_type": "",
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
            "running_without_controllers": False,
            "error_count": 1,
            "deployed_at": "2026-07-01T00:00:00Z",
            "error_logs": [{"msg": "e1"}],
            "general_logs": [{"msg": "g1"}],
        },
        {
            "bot_name": "zeta",
            "status": "stopped",
            "num_controllers": 0,
            "running_without_controllers": False,
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
    # The builder is pure and takes no executor rows, so the page it returns
    # carries the *unavailable* hygiene reading ([[FEAT-141]]) — the route folds
    # the real one from the executor cache. Unavailable rather than zeroed on
    # purpose: a clean answer invented from a reading nobody took is CORR-725.
    "executor_hygiene": {
        "available": False,
        "counted": 0,
        "running": 0,
        "terminated": 0,
        "truncated": False,
        "orphaned_running": [],
    },
}

# Captured from the pre-refactor ws_manager._transform_bots (no enrichment).
GOLDEN_WS = {
    "controllers": [
        {
            # No config, no class: never the id standing in for one.
            "controller_name": "",
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
            "controller_name": "",
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
            "running_without_controllers": False,
            "error_count": 1,
            "deployed_at": None,
            "error_logs": [{"msg": "e1"}],
            "general_logs": [{"msg": "g1"}],
        },
        {
            "bot_name": "zeta",
            "status": "stopped",
            "num_controllers": 0,
            "running_without_controllers": False,
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


def test_unenriched_transform_matches_pre_refactor_golden():
    """The degraded shape, for when a server cannot answer the enrichment calls.

    Both delivery paths now feed the builder the same enrichment (ARCH-586);
    what the empty maps still pin is how a page renders when those fetches fail.
    """
    assert build_bots_page(SAMPLE_RAW) == GOLDEN_WS


def test_live_zero_wins_over_stale_db_snapshot():
    """CORR-109: a legitimate live 0 must not fall back to the DB snapshot.

    Scenario: a controller was stopped at -50 realized / 1200 volume, then the
    same config id was redeployed. Live reports 0 for both; the dashboard must
    show 0, not the previous deploy's numbers.
    """
    raw = {
        "status": "success",
        "data": {
            "alpha": {
                "status": "running",
                "performance": {
                    "ctrl_a": {
                        "status": "running",
                        "performance": {
                            "realized_pnl_quote": 0.0,
                            "unrealized_pnl_quote": 0.0,
                            "global_pnl_pct": 0.0,
                            "volume_traded": 0.0,
                        },
                    },
                },
            },
        },
    }
    page = build_bots_page(
        raw,
        ctrl_configs={"ctrl_a": {"id": "cfg-a", "controller_name": "pmm_simple"}},
        latest_perf={
            "cfg-a": {
                "performance": {
                    "realized_pnl_quote": -50.0,
                    "unrealized_pnl_quote": -5.0,
                    "global_pnl_pct": -0.3,
                    "volume_traded": 1200.0,
                },
            },
        },
    )

    (row,) = page["controllers"]
    assert row["realized_pnl_quote"] == 0.0
    assert row["unrealized_pnl_quote"] == 0.0
    assert row["global_pnl_quote"] == 0.0
    assert row["global_pnl_pct"] == 0.0
    assert row["volume_traded"] == 0.0

    # Stale DB values must not leak into the server-wide totals either.
    assert page["total_pnl"] == 0.0
    assert page["total_volume"] == 0.0


def test_missing_live_field_still_falls_back_to_db_snapshot():
    """CORR-109: presence-based merge keeps the fallback for absent keys."""
    raw = {
        "status": "success",
        "data": {
            "alpha": {
                "status": "running",
                "performance": {
                    "ctrl_a": {
                        "status": "running",
                        # realized/volume omitted entirely -> DB snapshot wins
                        "performance": {"unrealized_pnl_quote": 1.0},
                    },
                },
            },
        },
    }
    page = build_bots_page(
        raw,
        ctrl_configs={"ctrl_a": {"id": "cfg-a", "controller_name": "pmm_simple"}},
        latest_perf={
            "cfg-a": {
                "performance": {
                    "realized_pnl_quote": -50.0,
                    "volume_traded": 1200.0,
                },
            },
        },
    )

    (row,) = page["controllers"]
    assert row["realized_pnl_quote"] == -50.0
    assert row["unrealized_pnl_quote"] == 1.0
    assert row["volume_traded"] == 1200.0
    assert page["total_pnl"] == -49.0
    assert page["total_volume"] == 1200.0


def test_live_empty_collections_win_over_stale_db_snapshot():
    """CORR-127: a live empty {} / [] must not fall back to the DB snapshot.

    Scenario: the same config id was redeployed. The new deploy has closed
    nothing and holds no positions, so live reports an empty histogram and an
    empty positions list; the dashboard must show empty, not the previous
    deploy's close reasons and open positions.
    """
    raw = {
        "status": "success",
        "data": {
            "alpha": {
                "status": "running",
                "performance": {
                    "ctrl_a": {
                        "status": "running",
                        "performance": {
                            "close_type_counts": {},
                            "positions_summary": [],
                        },
                    },
                },
            },
        },
    }
    page = build_bots_page(
        raw,
        ctrl_configs={"ctrl_a": {"id": "cfg-a", "controller_name": "pmm_simple"}},
        latest_perf={
            "cfg-a": {
                "performance": {
                    "close_type_counts": {"STOP_LOSS": 7},
                    "positions_summary": [{"pair": "ETH-USDT"}],
                },
            },
        },
    )

    (row,) = page["controllers"]
    assert row["close_type_counts"] == {}
    assert row["positions_summary"] == []


def test_missing_live_collections_still_fall_back_to_db_snapshot():
    """CORR-127: presence-based merge keeps the fallback for absent keys."""
    raw = {
        "status": "success",
        "data": {
            "alpha": {
                "status": "running",
                "performance": {
                    "ctrl_a": {
                        "status": "running",
                        # both collection keys omitted entirely -> DB snapshot wins
                        "performance": {"realized_pnl_quote": 1.0},
                    },
                },
            },
        },
    }
    page = build_bots_page(
        raw,
        ctrl_configs={"ctrl_a": {"id": "cfg-a", "controller_name": "pmm_simple"}},
        latest_perf={
            "cfg-a": {
                "performance": {
                    "close_type_counts": {"STOP_LOSS": 7},
                    "positions_summary": [{"pair": "ETH-USDT"}],
                },
            },
        },
    )

    (row,) = page["controllers"]
    assert row["close_type_counts"] == {"STOP_LOSS": 7}
    assert row["positions_summary"] == [{"pair": "ETH-USDT"}]


def test_extract_bots_list_handles_malformed_inputs():
    assert extract_bots_list(None) == []
    assert extract_bots_list("<html>error</html>") == []
    assert extract_bots_list({"status": "error", "message": "boom"}) == []
    assert extract_bots_list({"data": [{"bot_name": "a"}, "junk"]}) == [
        {"bot_name": "a"}
    ]
    assert extract_bots_list([{"bot_name": "b"}, 42]) == [{"bot_name": "b"}]


# ---------------------------------------------------------------------------
# CORR-725: "no reading" is unknown, not zero
# ---------------------------------------------------------------------------


def test_running_with_an_empty_performance_object_is_flagged():
    """GitHub #272: the container is up, the orchestrator says running, and the
    controller loop never iterates because ``performance`` came back empty. A
    bare ``status: "running"`` read as a healthy agent for 4h45m."""
    page = build_bots_page(
        {
            "status": "success",
            "data": {"ghost": {"status": "running", "performance": {}}},
        }
    )

    (bot,) = page["bots"]
    assert bot["num_controllers"] == 0
    assert bot["running_without_controllers"] is True
    # And the flag survives the REST response model rather than being dropped.
    (model,) = BotsPageResponse(**page).bots
    assert model.running_without_controllers is True


def test_a_stopped_bot_with_no_controllers_is_not_flagged():
    """The flag is "running with nothing running", not "has no controllers"."""
    page = build_bots_page(
        {
            "status": "success",
            "data": {"gone": {"status": "stopped", "performance": {}}},
        }
    )

    (bot,) = page["bots"]
    assert bot["num_controllers"] == 0
    assert bot["running_without_controllers"] is False


def test_a_controller_with_no_status_does_not_read_as_running():
    page = build_bots_page(
        {
            "status": "success",
            "data": {
                "epsilon": {
                    "status": "running",
                    "performance": {"ctrl_a": {"performance": {"volume_traded": 1.0}}},
                },
            },
        }
    )

    (row,) = page["controllers"]
    assert row["status"] == "unknown"
    # A bot with a controller is not the flagged shape, whatever that
    # controller's status turned out to be.
    assert page["bots"][0]["running_without_controllers"] is False
