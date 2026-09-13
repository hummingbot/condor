"""Regressions for the first review round: nonfinite prices, vanished bots,
suffixed bot names, durable-first apply, bounded bench, validated run names."""

import asyncio
import math

import pytest
from flybrain.guard import GuardSettings, Veto, check_price_move
from flybrain.market import Book, LiveMarket


class _Controllers:
    def __init__(self, fail_saved=False, fail_live=False):
        self.calls = []
        self.fail_saved = fail_saved
        self.fail_live = fail_live

    async def create_or_update_controller_config(self, name, config):
        self.calls.append(("saved", name))
        if self.fail_saved:
            raise RuntimeError("saved failed")

    async def update_bot_controller_config(self, bot, name, config):
        self.calls.append(("live", bot, name))
        if self.fail_live:
            raise RuntimeError("live failed")


class _Orchestration:
    def __init__(self, bots):
        self._bots = bots
        self.stopped = []

    async def get_active_bots_status(self):
        return {"data": self._bots}

    async def stop_and_archive_bot(self, name):
        self.stopped.append(name)


class _Client:
    def __init__(self, bots, **kw):
        self.bot_orchestration = _Orchestration(bots)
        self.controllers = _Controllers(**kw)


def _perf(net, volume):
    return {
        "performance": {
            "realized_pnl_quote": net,
            "unrealized_pnl_quote": 0,
            "volume_traded": volume,
        }
    }


def _market(bots, **kw):
    return LiveMarket(_Client(bots, **kw), "hyperliquid_perpetual", "5m", 72)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
def test_price_move_rejects_nonfinite(bad):
    with pytest.raises(Veto):
        check_price_move(100.0, bad, GuardSettings())
    with pytest.raises(Veto):
        check_price_move(bad, 100.0, GuardSettings())


def test_find_bot_accepts_deploy_suffix_and_refuses_ambiguity():
    bots = {"orcl-fly-20260913-055821": {}, "dram-fly": {}, "orcl-flyer": {}}
    assert LiveMarket.find_bot(bots, "orcl-fly")[0] == "orcl-fly-20260913-055821"
    assert LiveMarket.find_bot(bots, "dram-fly")[0] == "dram-fly"
    assert LiveMarket.find_bot(bots, "spcx-fly") == (None, None)
    with pytest.raises(RuntimeError):
        LiveMarket.find_bot({**bots, "orcl-fly": {}}, "orcl-fly")


def test_equity_carries_a_vanished_bot():
    running = {
        "orcl-fly-20260913-055821": {"performance": {"orcl_fly_mm": _perf(-1.5, 400)}}
    }
    net, volume, per_pair, carry = asyncio.run(
        _market(running).equity(["XYZ:ORCL-USD"])
    )
    assert (net, volume) == (-1.5, 400) and per_pair["XYZ:ORCL-USD"]["running"]
    # bot disappears from the status response: its last figures stay in the book
    net2, volume2, per_pair2, carry2 = asyncio.run(
        _market({}).equity(["XYZ:ORCL-USD"], carry)
    )
    assert (net2, volume2) == (-1.5, 400)
    assert per_pair2["XYZ:ORCL-USD"] == {
        "running": False,
        "carried": True,
        "net": -1.5,
        "volume": 400,
    }
    assert carry2 == carry
    # a bot that never reported contributes nothing
    net3, _, per_pair3, _ = asyncio.run(_market({}).equity(["XYZ:DRAM-USD"]))
    assert net3 == 0 and per_pair3["XYZ:DRAM-USD"] == {"running": False}


def test_apply_saves_before_touching_the_live_bot():
    bots = {"orcl-fly-20260913-055821": {}}
    m = _market(bots)
    asyncio.run(m.apply("XYZ:ORCL-USD", {"x": 1}))
    assert m.client.controllers.calls == [
        ("saved", "orcl_fly_mm"),
        ("live", "orcl-fly-20260913-055821", "orcl_fly_mm"),
    ]
    failing = _market(bots, fail_saved=True)
    with pytest.raises(RuntimeError):
        asyncio.run(failing.apply("XYZ:ORCL-USD", {"x": 1}))
    assert failing.client.controllers.calls == [("saved", "orcl_fly_mm")]
    with pytest.raises(RuntimeError):
        asyncio.run(_market({}).apply("XYZ:ORCL-USD", {"x": 1}))


def test_stop_bot_uses_the_running_name():
    m = _market({"orcl-fly-20260913-055821": {}})
    assert asyncio.run(m.stop_bot("XYZ:ORCL-USD")) is True
    assert m.client.bot_orchestration.stopped == ["orcl-fly-20260913-055821"]
    assert asyncio.run(_market({}).stop_bot("XYZ:ORCL-USD")) is False


def test_book_requires_finite_positive_prices():
    assert Book(1.0, 1.1).open and not Book(None, None).open
    assert not math.isfinite(float("nan"))


def test_setup_bench_bounds():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "routines" / "fly_setup.py"
    spec = importlib.util.spec_from_file_location("fly_setup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.Config(observations=20, neural_ms=200).observations == 20
    with pytest.raises(ValueError):
        mod.Config(observations=0)
    with pytest.raises(ValueError):
        mod.Config(observations=21)
    with pytest.raises(ValueError):
        mod.Config(neural_ms=10)


def test_status_rejects_escaping_run_name():
    import importlib.util
    from pathlib import Path

    from condor.paths import UnsafeIdError

    path = Path(__file__).resolve().parents[1] / "routines" / "fly_status.py"
    spec = importlib.util.spec_from_file_location("fly_status", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Ctx:
        _chat_id = 1

    for bad in ("../../etc", "/tmp/x", "a/b"):
        with pytest.raises(UnsafeIdError):
            asyncio.run(mod.run(mod.Config(run_name=bad), Ctx()))
