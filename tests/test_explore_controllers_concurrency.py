"""``explore_controllers`` fans its independent API calls out, not one by one.

A single ``manage_controllers(action="describe", config_name=..., include_code=True)``
used to cost five serial round trips to the Hummingbot API — usually a remote
host — even though only two of them depend on anything: the config template and
the source both need the controller *type*, and nothing else needs anything.
PERF-573 collapsed that into two waves.

The assertions here are about overlap, not just about the answer: the fake
client records which calls are in flight simultaneously, so the tests fail
against the sequential implementation (every observed in-flight set is a
singleton) rather than passing on both.

The repo has no async test setup, so the coroutines are driven with
asyncio.run() instead of a pytest-asyncio marker.
"""

import asyncio
import time

import pytest

from mcp_servers.hummingbot_api.tools.controllers import explore_controllers

CONTROLLERS = {
    "market_making": ["pmm_simple", "pmm_dynamic"],
    "directional_trading": ["macd_bb_v1"],
}

CONFIGS = [
    {"id": "pmm_simple_sol", "controller_name": "pmm_simple"},
    {"id": "pmm_simple_btc", "controller_name": "pmm_simple"},
    {"id": "macd_eth", "controller_name": "macd_bb_v1"},
]

TEMPLATE = {
    "id": {"type": "str", "default": None},
    "spread": {"type": "float", "default": 0.001},
}


class RecordingControllers:
    """Records, for every call, the set of calls in flight when it started."""

    def __init__(self, delay: float = 0.05, fail: dict | None = None):
        self.delay = delay
        self.fail = fail or {}
        self._inflight: set[str] = set()
        self.overlaps: list[frozenset[str]] = []
        self.calls: list[str] = []
        self.finished: list[str] = []

    async def _call(self, name: str, result):
        self.calls.append(name)
        self._inflight.add(name)
        self.overlaps.append(frozenset(self._inflight))
        try:
            await asyncio.sleep(self.delay)
            if name in self.fail:
                raise self.fail[name]
            return result
        finally:
            self._inflight.discard(name)
            self.finished.append(name)

    async def list_controllers(self):
        return await self._call("list_controllers", CONTROLLERS)

    async def list_controller_configs(self):
        return await self._call("list_controller_configs", CONFIGS)

    async def get_controller_config(self, config_name):
        return await self._call(
            "get_controller_config",
            {"id": config_name, "controller_name": "pmm_simple", "spread": 0.002},
        )

    async def get_controller_config_template(self, controller_type, controller_name):
        return await self._call("get_controller_config_template", TEMPLATE)

    async def get_controller(self, controller_type, controller_name):
        return await self._call("get_controller", "class PMMSimple: ...")


class FakeClient:
    def __init__(self, controllers):
        self.controllers = controllers


def _run(controllers, **kwargs):
    return asyncio.run(explore_controllers(client=FakeClient(controllers), **kwargs))


def test_describe_issues_two_waves_not_five_serial_calls():
    """The five calls of a full describe overlap into two waves."""
    fake = RecordingControllers()
    started = time.monotonic()
    result = _run(
        fake,
        action="describe",
        config_name="pmm_simple_sol",
        include_code=True,
    )
    elapsed = time.monotonic() - started

    assert len(fake.calls) == 5
    # Wave 1: both lists and the named config were in flight together.
    assert (
        frozenset(
            {"list_controllers", "list_controller_configs", "get_controller_config"}
        )
        in fake.overlaps
    ), f"wave 1 never overlapped: {fake.overlaps}"
    # Wave 2: template and source were in flight together.
    assert (
        frozenset({"get_controller_config_template", "get_controller"}) in fake.overlaps
    ), f"wave 2 never overlapped: {fake.overlaps}"
    # Two waves of 50ms, not five: allow plenty of slack for a loaded CI box.
    assert elapsed < 5 * fake.delay, f"{elapsed:.3f}s looks sequential"

    assert result["controller_name"] == "pmm_simple"
    assert result["controller_type"] == "market_making"
    assert result["template"] == TEMPLATE
    assert result["configs"] == ["pmm_simple_sol", "pmm_simple_btc"]
    assert result["controller_code"] == "class PMMSimple: ..."


def test_list_still_issues_only_the_two_lists_and_overlaps_them():
    """The list branch never fetches a config, and its two lists overlap."""
    fake = RecordingControllers()
    result = _run(fake, action="list")

    assert fake.calls == ["list_controllers", "list_controller_configs"]
    assert (
        frozenset({"list_controllers", "list_controller_configs"}) in fake.overlaps
    ), f"the two lists never overlapped: {fake.overlaps}"
    # Configs are indexed once and read per controller, with the same grouping.
    assert "- pmm_simple (2 configs)" in result["formatted_output"]
    assert "    - pmm_simple_sol\n" in result["formatted_output"]
    assert "- pmm_dynamic (0 configs)" in result["formatted_output"]
    assert "- macd_bb_v1 (1 configs)" in result["formatted_output"]


def test_describe_without_code_keeps_the_source_call_out_of_the_wave():
    fake = RecordingControllers()
    result = _run(fake, action="describe", controller_name="pmm_simple")

    assert "get_controller" not in fake.calls
    assert "get_controller_config" not in fake.calls
    assert "controller_code" not in result
    assert result["config_details"] is None
    assert "Tip: Set include_code=True" in result["formatted_output"]


def test_describe_output_orders_the_sections_as_before():
    fake = RecordingControllers(delay=0)
    out = _run(
        fake,
        action="describe",
        config_name="pmm_simple_sol",
        include_code=True,
    )["formatted_output"]

    assert out.index("Config 'pmm_simple_sol' Details:") < out.index("Controller: ")
    assert out.index("Controller: ") < out.index("Controller Code:")
    assert out.index("Controller Code:") < out.index("Configuration Parameters:")
    assert out.index("Configuration Parameters:") < out.index("Total Configs: 2")


def test_a_failing_leg_still_lets_its_siblings_settle():
    """One failure loses the whole answer, as before — but leaves no orphan.

    ``gather``'s default would propagate the first exception while the other
    legs kept running unawaited; here every leg finishes before the error is
    raised, and the error raised is the first *in call order*, which is the one
    the sequential code produced.
    """
    boom = RuntimeError("controllers listing is down")
    fake = RecordingControllers(
        fail={"list_controllers": boom, "list_controller_configs": ValueError("later")}
    )

    with pytest.raises(RuntimeError) as excinfo:
        _run(fake, action="describe", config_name="pmm_simple_sol")

    assert excinfo.value is boom
    assert set(fake.finished) == {
        "list_controllers",
        "list_controller_configs",
        "get_controller_config",
    }


def test_a_cancelled_leg_is_not_reported_as_our_own_cancellation():
    """A leg cancelled by someone else must not read as this task shutting down.

    ``gather(return_exceptions=True)`` hands a cancelled child back as a
    ``CancelledError`` *result*. Re-raising it verbatim would tell the caller it
    is being cancelled (CORR-332), so it surfaces as a plain failure instead.
    """
    fake = RecordingControllers(
        fail={"get_controller_config": asyncio.CancelledError()}
    )

    with pytest.raises(RuntimeError) as excinfo:
        _run(fake, action="describe", config_name="pmm_simple_sol")

    assert not isinstance(excinfo.value, asyncio.CancelledError)
    assert isinstance(excinfo.value.__cause__, asyncio.CancelledError)


def test_our_own_cancellation_still_propagates():
    """Cancelling the caller cancels the legs and raises CancelledError."""

    async def scenario():
        fake = RecordingControllers(delay=5)
        task = asyncio.ensure_future(
            explore_controllers(client=FakeClient(fake), action="list")
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(scenario())
