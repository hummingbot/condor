"""Controller run state and kill-switch writes in the hummingbot MCP bot tools.

The per-controller ``status`` the API reports is a hardcoded "running" that only
flips to "error" when the performance report fails to parse — it never reflects a
controller stopped via its kill switch. A trading agent read that field as proof
that ``stop_controllers`` had failed and stopped/redeployed whole bots for nothing,
so these tests pin the derived state and the write-then-verify behavior.
"""

import asyncio
import copy

import pytest

from mcp_servers.hummingbot_api.formatters import format_controller_state
from mcp_servers.hummingbot_api.tools import bot_management as bm

CONFIGS = [
    {"id": "eth_ctrl", "_config_name": "eth_ctrl", "manual_kill_switch": True},
    {"id": "btc_ctrl", "_config_name": "btc_ctrl", "manual_kill_switch": False},
    # config file name differs from the controller id the status payload uses
    {"id": "sol_ctrl", "_config_name": "sol_file", "manual_kill_switch": False},
]

STATUS = {
    "data": {
        "bot-1": {
            "status": "running",
            "performance": {
                "eth_ctrl": {"status": "running", "performance": {}},
                "btc_ctrl": {"status": "running", "performance": {}},
                "sol_ctrl": {"status": "error", "performance": {}},
                "ghost_ctrl": {"status": "running", "performance": {}},
                "bad_ctrl": "not-a-dict",
            },
        }
    }
}


class FakeControllers:
    def __init__(self, configs, fail_targets=(), configs_error=False):
        self.configs = [dict(c) for c in configs]
        self.fail_targets = set(fail_targets)
        self.configs_error = configs_error
        self.calls = []

    async def get_bot_controller_configs(self, bot_name):
        if self.configs_error:
            raise RuntimeError("bot not found")
        return self.configs

    async def update_bot_controller_config(self, bot_name, controller_name, config):
        self.calls.append((controller_name, dict(config)))
        if controller_name in self.fail_targets:
            raise RuntimeError("404 not found")
        for existing in self.configs:
            if controller_name in (existing.get("id"), existing.get("_config_name")):
                existing.update(config)
                return {"message": "ok"}
        raise RuntimeError("404 not found")


class FakeOrchestration:
    def __init__(self, data):
        self.data = data

    async def get_active_bots_status(self):
        return self.data


class FakeClient:
    def __init__(self, controllers, status=None):
        self.controllers = controllers
        # deep copy: get_active_bots_status annotates the payload in place
        self.bot_orchestration = FakeOrchestration(
            copy.deepcopy(status if status is not None else STATUS)
        )


def _make_client(**kwargs):
    return FakeClient(FakeControllers(CONFIGS, **kwargs))


@pytest.mark.parametrize(
    "controller_data,expected",
    [
        ({"status": "running", "kill_switch": True}, "stopped"),
        ({"status": "running", "kill_switch": False}, "running"),
        ({"status": "error", "kill_switch": False}, "error"),
        # kill switch wins over an error report: a stopped controller is stopped
        ({"status": "error", "kill_switch": True}, "stopped"),
        # no config could be read — never claim "running"
        ({"status": "running"}, "unknown"),
    ],
)
def test_format_controller_state(controller_data, expected):
    assert format_controller_state(controller_data) == expected


def test_status_reports_stopped_for_killed_controller():
    result = asyncio.run(bm.get_active_bots_status(_make_client()))
    performance = result["active_bots"]["data"]["bot-1"]["performance"]

    assert performance["eth_ctrl"]["kill_switch"] is True
    assert performance["btc_ctrl"]["kill_switch"] is False
    # a controller with no matching config stays unannotated rather than "running"
    assert "kill_switch" not in performance["ghost_ctrl"]

    table = result["bots_table"]
    assert "| eth_ctrl | stopped |" in table
    assert "| btc_ctrl | running |" in table
    assert "| ghost_ctrl | unknown |" in table


def test_status_survives_unreadable_controller_configs():
    result = asyncio.run(bm.get_active_bots_status(_make_client(configs_error=True)))

    # No kill switch is known, so every controller reads "unknown", not "running"
    assert "| eth_ctrl | unknown |" in result["bots_table"]


def test_stop_controllers_targets_the_config_file_name():
    client = _make_client()

    result = asyncio.run(
        bm.manage_bot_execution(
            client=client,
            bot_name="bot-1",
            action="stop_controllers",
            controller_names=["sol_ctrl"],
        )
    )

    # Addressed by _config_name, not by the controller id from the status payload
    assert client.controllers.calls == [("sol_file", {"manual_kill_switch": True})]
    assert result["succeeded"] == ["sol_ctrl"]
    assert result["confirmed_kill_switch"] == {"sol_ctrl": True}


def test_start_controllers_clears_the_kill_switch():
    client = _make_client()

    result = asyncio.run(
        bm.manage_bot_execution(
            client=client,
            bot_name="bot-1",
            action="start_controllers",
            controller_names=["eth_ctrl"],
        )
    )

    assert client.controllers.calls == [("eth_ctrl", {"manual_kill_switch": False})]
    assert result["confirmed_kill_switch"] == {"eth_ctrl": False}


def test_one_failing_controller_does_not_abort_the_others():
    client = _make_client(fail_targets=["btc_ctrl"])

    result = asyncio.run(
        bm.manage_bot_execution(
            client=client,
            bot_name="bot-1",
            action="stop_controllers",
            controller_names=["eth_ctrl", "btc_ctrl"],
        )
    )

    assert result["succeeded"] == ["eth_ctrl"]
    assert "btc_ctrl" in result["failed"]
    assert "Failed:" in result["message"]


def test_stop_raises_when_no_controller_could_be_stopped():
    client = _make_client()

    with pytest.raises(ValueError, match="No controller could be stopped"):
        asyncio.run(
            bm.manage_bot_execution(
                client=client,
                bot_name="bot-1",
                action="stop_controllers",
                controller_names=["nope"],
            )
        )


def test_stop_requires_controller_names():
    with pytest.raises(ValueError, match="controller_names is required"):
        asyncio.run(
            bm.manage_bot_execution(
                client=_make_client(),
                bot_name="bot-1",
                action="stop_controllers",
                controller_names=[],
            )
        )
