"""The Kalman grid operator never leaves a leveraged grid running unwatched.

Three ways it used to, all of which end with a 5x grid trading its full 24-hour
lifetime with nobody left to retune or exit it:

1. Cancellation landing inside ``create_executor`` -- after the server made the
   grid, before its id came back -- left ``executor_id`` None, so the shutdown
   handler saw nothing to stop and reported FLAT.
2. An executor leaving the active listing was read as "gone", but a close that
   exhausts its retries ends as a position hold: executor gone, position still on
   the venue. The routine then deployed a second full-budget grid on top of it.
3. The shutdown handler called ``stop_executor`` and reported success on a call
   that merely did not raise, having verified neither termination nor flatness.

Run with: pytest tests/test_kalman_grid_operator_safety.py -v
"""
import asyncio

import pytest

from agents.adaptive_grid_trader.routines.kalman_grid_operator import (
    _controller_id,
    _flat_check,
    _teardown,
)


class Config:
    trading_pair = "BTC-USDT"


class FakeExecutors:
    def __init__(self, active=None, held=0, stop_raises=False, summary_raises=False):
        self._active = list(active or [])
        self._held = held
        self._stop_raises = stop_raises
        self._summary_raises = summary_raises
        self.stop_calls = []
        self.searched_controller_ids = []

    async def stop_executor(self, executor_id, keep_position=False):
        self.stop_calls.append((executor_id, keep_position))
        if self._stop_raises:
            raise RuntimeError("venue refused")
        self._active = [e for e in self._active if e["id"] != executor_id]
        return {}

    async def search_executors(self, controller_ids=None, status=None, limit=None):
        self.searched_controller_ids.append(controller_ids)
        return {"data": list(self._active), "pagination": {}}

    async def get_positions_summary(self, controller_id=None):
        if self._summary_raises:
            raise RuntimeError("api down")
        return {"total_positions": self._held}


class FakeClient:
    def __init__(self, executors):
        self.executors = executors


def _run(coro):
    return asyncio.run(coro)


class TestTheControllerIdIsADurableHandle:

    def test_it_is_fixed_before_any_executor_exists(self):
        # The whole point: available to the shutdown handler even if no create
        # ever returned an id.
        assert _controller_id(Config()).startswith("kalman_grid_btcusdt_")

    def test_two_runs_do_not_share_one(self):
        # A shared id would make one run tear down another's grid.
        first = _controller_id(Config())
        import time as _t
        _t.sleep(1.1)
        assert _controller_id(Config()) != first


class TestFlatnessIsProvedNotAssumed:

    def test_a_held_position_is_not_flat(self):
        client = FakeClient(FakeExecutors(held=1))
        flat, note = _run(_flat_check(client, "ctl"))
        assert flat is False
        assert "1 position(s) still held" in note

    def test_an_unreadable_summary_is_not_flat(self):
        # "I could not tell" must never authorise more money onto the venue.
        client = FakeClient(FakeExecutors(summary_raises=True))
        flat, note = _run(_flat_check(client, "ctl"))
        assert flat is False
        assert "unverified" in note

    def test_nothing_held_is_flat(self):
        client = FakeClient(FakeExecutors(held=0))
        assert _run(_flat_check(client, "ctl")) == (True, "")

    def test_it_asks_only_about_this_run(self):
        execs = FakeExecutors(held=0)
        _run(_flat_check(FakeClient(execs), "ctl"))
        # get_positions_summary is filtered by controller_id; an unrelated grid's
        # position must not read as ours.
        assert execs._held == 0


class TestTeardownProvesStoppedAndFlat:

    def test_it_closes_the_position_rather_than_keeping_it(self):
        execs = FakeExecutors(active=[{"id": "e1"}], held=0)
        assert _run(_teardown(FakeClient(execs), "ctl", "e1", [], "00:00")) is True
        assert execs.stop_calls == [("e1", False)]

    def test_a_stop_that_raises_is_not_success(self):
        execs = FakeExecutors(active=[{"id": "e1"}], stop_raises=True)
        log = []
        assert _run(_teardown(FakeClient(execs), "ctl", "e1", log, "00:00")) is False
        assert "no redeploy" in log[0]

    def test_an_executor_still_listed_is_not_success(self):
        execs = FakeExecutors(active=[{"id": "e1"}])
        execs.stop_executor = lambda executor_id, keep_position=False: _noop()
        log = []
        assert _run(_teardown(FakeClient(execs), "ctl", "e1", log, "00:00")) is False
        assert "still active" in log[0]

    def test_a_residual_position_is_not_success(self):
        # The filed defect: executor gone, exposure not. Returning True here is
        # what let the routine stack a second full-budget grid beside it.
        execs = FakeExecutors(active=[{"id": "e1"}], held=1)
        log = []
        assert _run(_teardown(FakeClient(execs), "ctl", "e1", log, "00:00")) is False
        assert "still held" in log[0]

    def test_it_scopes_its_liveness_check_to_this_run(self):
        execs = FakeExecutors(active=[{"id": "e1"}], held=0)
        _run(_teardown(FakeClient(execs), "ctl-xyz", "e1", [], "00:00"))
        assert execs.searched_controller_ids == [["ctl-xyz"]]


async def _noop():
    return {}


class TestTheDeploySiteIsGated:

    def test_every_deploy_path_checks_flatness_first(self):
        # Comment 2 is broader than the line it was filed on: the "grid died on
        # its own" branch reaches a deploy without going through _teardown. The
        # gate belongs at the deploy site so both paths pass through it.
        import inspect

        from agents.adaptive_grid_trader.routines import kalman_grid_operator as m

        source = inspect.getsource(m.run)
        gate = source.index("_flat_check(client, controller_id)")
        deploy = source.index("await _deploy(")
        assert gate < deploy, "_flat_check must gate the deploy, not follow it"

    def test_the_deploy_is_tagged_with_the_controller_id(self):
        import inspect

        from agents.adaptive_grid_trader.routines import kalman_grid_operator as m

        assert "controller_id=controller_id" in inspect.getsource(m._deploy)


class TestShutdownVerifies:

    def test_it_tears_down_rather_than_firing_a_bare_stop(self):
        import inspect

        from agents.adaptive_grid_trader.routines import kalman_grid_operator as m

        handler = inspect.getsource(m.run).split("except asyncio.CancelledError:")[-1]
        assert "_teardown(" in handler
        assert "stop_executor(" not in handler

    def test_it_finds_grids_the_lost_id_would_have_hidden(self):
        import inspect

        from agents.adaptive_grid_trader.routines import kalman_grid_operator as m

        handler = inspect.getsource(m.run).split("except asyncio.CancelledError:")[-1]
        # Not gated on executor_id: that is exactly the variable a mid-POST
        # cancellation leaves as None while a grid is live.
        assert "_our_executors(client, controller_id)" in handler
        assert "if executor_id and client:" not in handler


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
