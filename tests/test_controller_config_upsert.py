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

# ----------------------------------------------------------------------
# Review follow-ups (Greptile on PR #330)
# ----------------------------------------------------------------------


def test_a_truncated_nested_object_is_not_reported_as_identical():
    """Every key sent must appear in the stored dict, not the other way round.

    A backend that keeps one of a nested object's settings -- or none -- used to
    compare as identical, because the check asked whether the *stored* keys were
    a subset of the sent ones.
    """
    from mcp_servers.hummingbot_api.tools.controllers import _fields_that_did_not_land

    sent = {"trailing_stop": {"activation_price": 1, "trailing_delta": 0.5}}
    assert _fields_that_did_not_land(sent, {"trailing_stop": {"activation_price": 1}})
    assert _fields_that_did_not_land(sent, {"trailing_stop": {}})
    assert _fields_that_did_not_land(
        sent, {"trailing_stop": {"activation_price": 1, "trailing_delta": 0.5}}
    ) == []


def test_extra_stored_defaults_are_not_a_lost_field():
    from mcp_servers.hummingbot_api.tools.controllers import _fields_that_did_not_land

    sent = {"total_amount_quote": 12}
    stored = {"total_amount_quote": 12, "some_backend_default": True}
    assert _fields_that_did_not_land(sent, stored) == []


def test_the_read_error_is_carried_into_the_failure():
    """'timed out' and 'not there' are the same verdict, not the same incident."""

    class TimedOutControllers(ScriptedControllers):
        async def get_controller_config(self, config_name):
            raise TimeoutError("read-back timed out after 30s")

    client = ScriptedClient()
    client.controllers = TimedOutControllers()

    with pytest.raises(RuntimeError) as exc:
        _upsert(client, confirm_override=True)

    assert "not readable afterwards" in str(exc.value)
    assert "timed out" in str(exc.value), "the reason the read failed must survive"


def test_the_public_tool_returns_the_verification_fields():
    """The agent reads the tool result, so the fields have to be in it."""
    from mcp_servers.hummingbot_api.tools.controllers import manage_controllers

    stored = {k: v for k, v in CONFIG.items() if k != "total_amount_quote"}
    client = ScriptedClient(stored_after=stored)

    result = asyncio.run(
        manage_controllers(
            client=client,
            action="upsert",
            target="config",
            config_name=CONFIG["id"],
            config_data=dict(CONFIG),
            confirm_override=True,
        )
    )

    assert result["wrote"] is True
    assert result["outcome"] in ("created", "updated")
    assert "total_amount_quote" in result["unverified_fields"]
