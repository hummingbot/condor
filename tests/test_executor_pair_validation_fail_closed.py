"""Tests for CORR-132: the executor panels must fail closed around the validator.

[[CORR-124]] made ``validate_trading_pair`` itself fail closed, but the grid and
position wizards still wrapped the whole validation block in a bare
``except Exception`` whose fall-through path assigned and persisted the pair
anyway. Anything raised *around* the validator — the API client lookup, the
``get_correct_pair_format`` fallback read — therefore still landed an
unvalidated pair in the executor config with only a log warning.

Both panels now treat a raised exception exactly like an invalid pair: nothing
is persisted, and the user lands on the same suggestions screen. The two causes
stay distinguishable in the message the user reads.
"""

import asyncio
from types import SimpleNamespace

import pytest

from handlers.executors import grid, position

RULES = {"BTC-USDT": {"min_order_size": 1}}

# Each panel duplicates the block verbatim, so every test runs against both.
PANELS = [
    pytest.param((grid, "show_step_2_combined"), id="grid"),
    pytest.param((position, "show_step_2_config"), id="position"),
]

both_panels = pytest.mark.parametrize("panel", PANELS, indirect=True)


def _boom():
    raise RuntimeError("502 Bad Gateway")


class _Panel:
    """Drives one wizard's ``handle_pair_input`` with its collaborators stubbed."""

    def __init__(self, module, step_2_name, monkeypatch):
        self.module = module
        self.monkeypatch = monkeypatch
        self.shown = None
        self.reached_step_2 = False
        self.context = SimpleNamespace(
            user_data={
                "executor_config_params": {"connector_name": "binance_perpetual"}
            }
        )
        self.update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1), message=None, callback_query=None
        )

        async def show_pair_suggestions(
            update, context, error_msg, suggestions, **wiring
        ):
            self.shown = {
                "error_msg": error_msg,
                "suggestions": suggestions,
                **wiring,
            }

        async def step_2(update, context):
            self.reached_step_2 = True

        self._patch("_show_pair_suggestions", show_pair_suggestions)
        self._patch(step_2_name, step_2)
        self.set_client(lambda: (object(), None))
        self.set_validator(lambda: (True, None, [], "BTC-USDT"))
        self.set_trading_rules(lambda: RULES)

    def _patch(self, name, fn):
        self.monkeypatch.setattr(self.module, name, fn)

    def set_client(self, produce):
        async def get_executors_client(chat_id, user_data):
            return produce()

        self._patch("get_executors_client", get_executors_client)

    def set_validator(self, produce):
        async def validate_trading_pair(user_data, client, connector, pair):
            return produce()

        self._patch("validate_trading_pair", validate_trading_pair)

    def set_trading_rules(self, produce):
        async def get_trading_rules(user_data, client, connector):
            return produce()

        self._patch("get_trading_rules", get_trading_rules)

    def run(self, pair):
        asyncio.run(self.module.handle_pair_input(self.update, self.context, pair))

    @property
    def config(self):
        return self.context.user_data["executor_config_params"]


@pytest.fixture
def panel(request, monkeypatch):
    module, step_2_name = request.param
    return _Panel(module, step_2_name, monkeypatch)


@both_panels
def test_a_raising_validator_does_not_persist_the_pair(panel):
    """The whole point: an exception must not wave an unvalidated pair through."""
    panel.set_validator(_boom)

    panel.run("DOGE-USDT")

    assert "trading_pair" not in panel.config
    assert panel.reached_step_2 is False
    assert panel.shown is not None


@both_panels
def test_a_failure_before_the_validator_does_not_persist_the_pair(panel):
    """The client lookup is inside the block, so its failure must fail closed too."""
    panel.set_client(_boom)

    panel.run("DOGE-USDT")

    assert "trading_pair" not in panel.config
    assert panel.reached_step_2 is False
    assert panel.shown is not None


@both_panels
def test_a_raising_pair_format_fallback_does_not_persist_the_pair(panel):
    """The fallback read runs after a valid verdict, and still gates the write."""
    panel.set_validator(lambda: (True, None, [], None))
    panel.set_trading_rules(_boom)

    panel.run("BTC-USDT")

    assert "trading_pair" not in panel.config
    assert panel.reached_step_2 is False
    assert panel.shown is not None


@both_panels
def test_a_crash_and_an_invalid_pair_share_the_surface_but_not_the_cause(panel):
    """Same screen for both, but the user can still tell them apart."""
    panel.set_validator(lambda: (False, "DOGE-USDT not found", ["DOGE-USDC"], None))
    panel.run("DOGE-USDT")
    invalid = panel.shown

    panel.shown = None
    panel.set_validator(_boom)
    panel.run("DOGE-USDT")
    crashed = panel.shown

    # Same surface: neither persists, both land on the suggestions screen.
    assert "trading_pair" not in panel.config
    assert invalid is not None
    assert crashed is not None

    # Different cause: "that pair is wrong" vs "we could not check".
    assert invalid["error_msg"] == "DOGE-USDT not found"
    assert invalid["suggestions"] == ["DOGE-USDC"]
    assert "could not reach" in crashed["error_msg"].lower()
    assert "binance_perpetual" in crashed["error_msg"]
    assert crashed["suggestions"] == []


@both_panels
def test_a_validated_pair_is_still_persisted_in_the_exchange_format(panel):
    """Fail-closed must not block the happy path."""
    panel.set_validator(lambda: (True, None, [], "BTC-USDT"))

    panel.run("btc/usdt")

    assert panel.config["trading_pair"] == "BTC-USDT"
    assert panel.reached_step_2 is True
    assert panel.shown is None


@both_panels
def test_the_pair_format_fallback_still_applies_when_validation_returns_none(panel):
    """A valid verdict without a formatted pair still goes through the fallback."""
    panel.set_validator(lambda: (True, None, [], None))

    panel.run("BTC-USDT")

    assert panel.config["trading_pair"] == "BTC-USDT"
    assert panel.reached_step_2 is True
