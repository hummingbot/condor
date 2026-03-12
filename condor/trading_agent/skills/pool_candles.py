"""Core skill: fetch OHLCV candles for a Solana pool from GeckoTerminal."""

from __future__ import annotations

import logging
from typing import Any

from . import register_skill
from .base import BaseSkill, SkillResult

log = logging.getLogger(__name__)


class PoolCandlesSkill(BaseSkill):
    name = "pool_candles"
    is_core = True

    async def execute(self, client: Any, config: dict, agent_id: str = "") -> SkillResult:
        pool_address = config.get("pool_address", "")
        if not pool_address:
            return SkillResult(
                name=self.name,
                data={"error": "pool_address required in config"},
                summary="Pool Candles: no pool_address provided",
            )

        network = config.get("network", "solana")
        timeframe = config.get("timeframe", "hour")
        limit = config.get("candle_limit", 168)  # 7 days of hourly candles

        try:
            from geckoterminal_py import GeckoTerminalAsyncClient

            gecko = GeckoTerminalAsyncClient()
            result = await gecko.get_ohlcv(
                network_id=network,
                pool_address=pool_address,
                timeframe=timeframe,
                limit=limit,
            )

            # Parse result (DataFrame or raw dict)
            candles = []
            try:
                import pandas as pd
                if isinstance(result, pd.DataFrame) and not result.empty:
                    candles = result.to_dict("records")
            except ImportError:
                pass

            if not candles:
                if isinstance(result, list):
                    candles = result
                elif isinstance(result, dict):
                    ohlcv_list = result.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                    candles = [
                        {"timestamp": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]}
                        for c in ohlcv_list
                    ]

            if not candles:
                return SkillResult(
                    name=self.name,
                    data={"candles": [], "pool_address": pool_address},
                    summary=f"Pool Candles ({pool_address[:8]}...): no data from GeckoTerminal",
                )

            # Extract high/low/current from candle data
            highs = []
            lows = []
            for c in candles:
                h = c.get("high")
                l = c.get("low")
                if h is not None:
                    highs.append(float(h))
                if l is not None:
                    lows.append(float(l))

            current_price = float(candles[-1]["close"]) if candles[-1].get("close") else None
            period_high = max(highs) if highs else None
            period_low = min(lows) if lows else None

            summary_data = {
                "candles": candles,
                "pool_address": pool_address,
                "current_price": current_price,
                "period_high": period_high,
                "period_low": period_low,
                "candle_count": len(candles),
                "timeframe": timeframe,
            }

            lines = [f"Pool Candles ({pool_address[:8]}..., {timeframe}, {len(candles)} candles):"]
            if current_price is not None:
                lines.append(f"  Current: ${current_price:,.6g}")
            if period_high is not None and period_low is not None:
                lines.append(f"  High: ${period_high:,.6g} | Low: ${period_low:,.6g}")
                if period_low > 0:
                    range_pct = ((period_high - period_low) / period_low) * 100
                    lines.append(f"  Range: {range_pct:,.1f}%")

            return SkillResult(
                name=self.name,
                data=summary_data,
                summary="\n".join(lines),
            )

        except Exception as e:
            log.exception("PoolCandlesSkill failed")
            return SkillResult(
                name=self.name,
                data={"error": str(e), "pool_address": pool_address},
                summary=f"Pool Candles ({pool_address[:8]}...): failed ({e})",
            )


register_skill(PoolCandlesSkill())
