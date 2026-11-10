"""Tests: a config upsert must not report a write it cannot prove."""
import asyncio

import pytest

from mcp_servers.hummingbot_api.tools.controllers import modify_controllers

CONFIG = {
    "id": "probe_cfg",
    "controller_type": "generic",
    "controller_name": "stable_churn",
    "trading_pair": "USD1-USDT",
    "total_amount_quote": 12,
}


class ScriptedControllers:
    """A client whose write and read-back can disagree, as the live one did."""

    def __init__(self, *, stored_after=None, readback_raises=False, existing=()):
        self.stored_after = stored_after
        self.readback_raises = readback_raises
        self.existing = list(existing)
        self.writes: list[tuple[str, dict]] = []

    async def get_controller_config_template(self, controller_type, controller_name):
        return {k: {"default": 0} for k in CONFIG}

    async def validate_controller_config(self, controller_type, controller_name, config):
        return {"valid": True}

    async def list_controller_configs(self):
        return [{"id": name} for name in self.existing]

    async def create_or_update_controller_config(self, config_name, config):
        self.writes.append((config_name, config))
        return {"message": f"Configuration '{config_name}' saved successfully"}

    async def get_controller_config(self, config_name):
        if self.readback_raises:
            raise RuntimeError("404 Configuration not found")
        if self.stored_after is None:
            raise RuntimeError("404 Configuration not found")
        return self.stored_after


class ScriptedClient:
    def __init__(self, **kwargs):
        self.controllers = ScriptedControllers(**kwargs)


def _upsert(client, **kwargs):
    return asyncio.run(
        modify_controllers(
            client,
            action="upsert",
            target="config",
            config_name=CONFIG["id"],
            config_data=dict(CONFIG),
            **kwargs,
        )
    )


def test_a_write_that_lands_reports_creation():
    a = ScriptedClient(stored_after=dict(CONFIG))
    result = _upsert(a, confirm_override=True)

    assert a.controllers.writes, "the config must actually be written"
    assert result["outcome"] == "created"
    assert result["wrote"] is True
    assert result["unverified_fields"] == []


def test_a_write_the_api_accepts_but_cannot_read_back_fails():
    """The reported bug: 200 from the API, nothing on GET, success in the journal.

    Told the write landed, the agent deploys that config name and the bot dies on
    a controller file that was never written. It must fail here instead.
    """
    a = ScriptedClient(readback_raises=True)

    with pytest.raises(RuntimeError) as exc:
        _upsert(a, confirm_override=True)

    assert "not readable afterwards" in str(exc.value)
    assert a.controllers.writes, "the write was attempted; that is not the point"


def test_a_read_back_that_returns_nothing_fails_too():
    a = ScriptedClient(stored_after=None)
    with pytest.raises(RuntimeError):
        _upsert(a, confirm_override=True)


def test_no_override_is_reported_as_no_write():
    """An existing config without confirm_override writes nothing and says so."""
    a = ScriptedClient(stored_after=dict(CONFIG), existing=[CONFIG["id"]])
    result = _upsert(a, confirm_override=False)

    assert a.controllers.writes == []
    assert result["outcome"] == "unchanged"
    assert result["wrote"] is False
    assert result["exists"] is True


def test_fields_the_backend_dropped_are_reported():
    stored = {k: v for k, v in CONFIG.items() if k != "total_amount_quote"}
    a = ScriptedClient(stored_after=stored)
    result = _upsert(a, confirm_override=True)

    assert result["wrote"] is True
    assert "total_amount_quote" in result["unverified_fields"]


def test_a_normalised_number_is_not_mistaken_for_a_lost_field():
    stored = dict(CONFIG, total_amount_quote=12.0)
    a = ScriptedClient(stored_after=stored)
    result = _upsert(a, confirm_override=True)

    assert result["unverified_fields"] == []
