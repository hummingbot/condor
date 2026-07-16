"""Unit tests for the human-readable confirmation-prompt formatter."""

from condor.agents.confirmation import _format_tool_summary


def _exec_call(action: str, **extra) -> dict:
    return {"tool": "manage_executors", "input": {"action": action, **extra}}


def test_format_native_create_shows_type_and_instrument():
    summary = _format_tool_summary(
        _exec_call(
            "create",
            executor_type="position_spot",
            config={"trading_pair": "BONK-SOL"},
        )
    )
    assert "position_spot" in summary
    assert "BONK-SOL" in summary

    perp = _format_tool_summary(
        _exec_call("create", executor_type="position_perp", config={"coin": "ETH"})
    )
    assert "position_perp" in perp and "ETH" in perp


def test_format_stop_shows_executor_id():
    summary = _format_tool_summary(
        _exec_call("stop", executor_id="pos_abcdef1234567890")
    )
    assert "Stop executor" in summary
    assert "pos_abcdef12" in summary


def test_format_unknown_tool_falls_back_to_name():
    assert _format_tool_summary({"tool": "weird_tool", "input": {}}) == "weird_tool"
