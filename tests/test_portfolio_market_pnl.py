"""Portfolio P&L is what prices did, not what was deposited (#350)."""

import asyncio

from condor.web.routes.portfolio import get_portfolio_history


class _Sds:
    def __init__(self, history):
        self.history = history

    async def get_or_fetch(self, *args, **kwargs):
        return self.history


def _snap(ts, *rows):
    balances = [
        {"token": token, "units": units, "value": units * price}
        for token, units, price in rows
    ]
    return {"timestamp": ts, "state": {"master": {"binance": balances}}}


def _pnl(monkeypatch, *snaps):
    monkeypatch.setattr(
        "condor.web.routes.portfolio.get_server_data_service",
        lambda: _Sds(list(snaps)),
    )
    res = asyncio.run(
        get_portfolio_history("s", range="1D", breakdown=False, user=None)
    )
    return [round(p.pnl_usd, 6) for p in res.points]


def test_a_deposit_is_not_a_gain(monkeypatch):
    assert _pnl(
        monkeypatch, _snap(1, ("USDT", 100, 1)), _snap(2, ("USDT", 300, 1))
    ) == [0, 0]


def test_a_loss_shows_through_a_deposit(monkeypatch):
    # SOL falls 150 -> 120 while $200 of USDT comes in.
    assert _pnl(
        monkeypatch,
        _snap(1, ("SOL", 1, 150)),
        _snap(2, ("SOL", 1, 120), ("USDT", 200, 1)),
    ) == [0, -30]


def test_a_withdrawal_is_not_a_loss(monkeypatch):
    # 2 BTC held as the price goes 100 -> 110 (+20); 1 BTC withdrawn.
    assert _pnl(monkeypatch, _snap(1, ("BTC", 2, 100)), _snap(2, ("BTC", 1, 110))) == [
        0,
        20,
    ]
