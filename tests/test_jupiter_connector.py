"""JupiterConnector: quote price math + execute fill parsing (fakes, no network)."""

import asyncio
from decimal import Decimal

import pytest

from condor.executors.jupiter import JupiterConnector
from condor.executors.solana import WSOL_MINT, TxResult

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MEME = "MemeMint1111111111111111111111111111111111"


class FakeSolana:
    def __init__(self, tx_result=None):
        self._decimals = {WSOL_MINT: 9, USDC: 6, MEME: 6}
        self.tx_result = tx_result
        self.sent = []

    async def token_decimals(self, mint):
        return self._decimals[mint]

    async def sign_and_send(self, tx_b64):
        self.sent.append(tx_b64)
        return "sig-xyz"

    async def poll(self, signature):
        return self.tx_result

    async def status(self, signature):
        return 1

    async def balance(self, mint=None):
        return Decimal("3")


class FakeJupiter:
    def __init__(self, out_amount=None, in_amount=None):
        self.out_amount = out_amount
        self.in_amount = in_amount
        self.quotes = []

    async def quote(self, input_mint, output_mint, amount_atomic, slippage_bps, swap_mode="ExactIn"):
        self.quotes.append((input_mint, output_mint, amount_atomic, slippage_bps, swap_mode))
        q = {"inputMint": input_mint, "outputMint": output_mint, "swapMode": swap_mode}
        if self.out_amount is not None:
            q["outAmount"] = str(self.out_amount)
        if self.in_amount is not None:
            q["inAmount"] = str(self.in_amount)
        return q

    async def build_swap_tx(self, quote_resp, user_pubkey, priority_lamports=None):
        return "base64tx"


def _conn(sol, jup):
    c = JupiterConnector.__new__(JupiterConnector)
    c.solana = sol
    c.jupiter = jup
    return c


def test_quote_sell_price_is_quote_per_base():
    # SELL 2 SOL -> USDC, out = 300 USDC (6 dec) => price 150 USDC/SOL
    jup = FakeJupiter(out_amount=300_000_000)
    c = _conn(FakeSolana(), jup)
    q = asyncio.run(c.quote_swap("solana-mainnet-beta", "SOL", "USDC", 2.0, "SELL", slippage_pct=1.0))
    assert q["amountOut"] == pytest.approx(300.0)
    assert q["price"] == pytest.approx(150.0)
    # ExactIn, amount in lamports, slippage 100 bps
    assert jup.quotes[0] == (WSOL_MINT, USDC, 2_000_000_000, 100, "ExactIn")


def test_quote_buy_uses_exactout_and_input_amount():
    # BUY 1 SOL (base) with USDC, in = 151 USDC => price 151 USDC/SOL
    jup = FakeJupiter(in_amount=151_000_000)
    c = _conn(FakeSolana(), jup)
    q = asyncio.run(c.quote_swap("solana-mainnet-beta", "SOL", "USDC", 1.0, "BUY", slippage_pct=0.5))
    assert q["amountIn"] == pytest.approx(151.0)
    assert q["price"] == pytest.approx(151.0)
    assert jup.quotes[0][4] == "ExactOut"
    assert jup.quotes[0][3] == 50  # 0.5% -> 50 bps


def test_execute_uses_actual_spl_delta_for_received():
    # SELL SOL -> MEME; quote says 1000 MEME, chain delta says 995 (slippage) -> use 995
    res = TxResult(signature="sig-xyz", confirmed=True, fee_lamports=5000,
                   token_deltas={MEME: Decimal("995")}, sol_delta_lamports=-2_000_000_000)
    jup = FakeJupiter(out_amount=1000_000000)  # 1000 MEME (6 dec)
    c = _conn(FakeSolana(tx_result=res), jup)
    out = asyncio.run(c.execute_swap("solana-mainnet-beta", "W", "SOL", MEME, 2.0, "SELL"))
    assert out["status"] == 1
    assert out["data"]["amountOut"] == pytest.approx(995.0)  # actual, not quoted 1000
    assert out["data"]["fee"] == pytest.approx(0.000005)


def test_execute_sol_out_adds_fee_back():
    # SELL MEME -> SOL; received SOL = sol_delta + fee
    res = TxResult(signature="sig-xyz", confirmed=True, fee_lamports=5000,
                   token_deltas={MEME: Decimal("-500")}, sol_delta_lamports=1_500_000_000)
    jup = FakeJupiter(out_amount=1_500_005000)
    c = _conn(FakeSolana(tx_result=res), jup)
    out = asyncio.run(c.execute_swap("solana-mainnet-beta", "W", MEME, "SOL", 500.0, "SELL"))
    assert out["data"]["amountOut"] == pytest.approx((1_500_000_000 + 5000) / 1e9)
    assert out["data"]["amountIn"] == pytest.approx(500.0)  # abs SPL delta


def test_poll_tx_maps_status():
    c = _conn(FakeSolana(), FakeJupiter())
    assert asyncio.run(c.poll_tx("solana", "mainnet-beta", "sig"))["txStatus"] == 1
