"""Unit tests for the human-readable confirmation-prompt formatter."""

from handlers.agents.confirmation import _format_tool_summary


def _bot_call(action: str, **extra) -> dict:
    return {"tool": "manage_bots", "input": {"action": action, **extra}}


def test_format_manage_bots_deploy():
    summary = _format_tool_summary(
        _bot_call("deploy", bot_name="mm-bot", controllers_config=["cfg_a"])
    )
    assert "mm-bot" in summary
    assert "cfg_a" in summary


def test_format_manage_bots_update_config():
    summary = _format_tool_summary(
        _bot_call("update_config", bot_name="mm-bot", config_name="cfg_a")
    )
    assert "mm-bot" in summary
    assert "cfg_a" in summary


def test_format_manage_bots_stop_bot():
    summary = _format_tool_summary(_bot_call("stop_bot", bot_name="mm-bot"))
    assert "mm-bot" in summary
    assert "stop_bot" in summary
