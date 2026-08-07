---
name: controller_development
description: Phase 2 — turn a confirmed signal spec into a complete, fully vectorized
  Hummingbot v2 directional controller, uploaded with an initial config
when_to_use: When a signal spec from research is confirmed and you need to create,
  modify, debug, or upload a directional controller or its config
created: '2026-07-30T20:06:03Z'
source: agent:directional_trader
---

# Phase 2: Controller Development

Translate the confirmed signal spec into a working controller — fully vectorized,
uploaded, with an initial named config ready for backtesting.

## Step 1 — Define the config class

Every tunable value is a config field. **No magic numbers in the signal logic** —
a hardcoded threshold cannot be swept, which kills Phase 3.

Required fields (from the base class):
- `controller_name` — snake_case and descriptive (`ema_cross_adx_filter`)
- `connector_name` — e.g. `binance_perpetual`
- `trading_pair` — e.g. `BTC-USDT`
- `candles_connector` / `candles_trading_pair` — usually mirror the above
- `interval` — from research (e.g. `1h`)
- `max_records` — `max(indicator_length) * 3`, minimum 200

## Step 2 — Implement `update_processed_data`

**Critical rules:**
1. **Fully vectorized** — no `iterrows()`, no `apply()` with Python lambdas, no
   loops over rows. The engine reads the signal column in bulk; a row-loop
   silently produces wrong results.
2. The `signal` column holds only `1`, `-1` or `0` — not booleans, not floats.
3. Every tunable value comes from `self.config.<param>`.
4. End with exactly these two exports.

```python
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
    DirectionalTradingControllerConfigBase,
)
import pandas_ta as ta
import pandas as pd


class EmaCrossAdxConfig(DirectionalTradingControllerConfigBase):
    controller_name: str = "ema_cross_adx"
    connector_name: str = "binance_perpetual"
    trading_pair: str = "BTC-USDT"
    candles_connector: str = "binance_perpetual"
    candles_trading_pair: str = "BTC-USDT"
    interval: str = "1h"
    max_records: int = 500
    # ---- strategy params (one field per tunable value) ----
    ema_fast: int = 20
    ema_slow: int = 50
    adx_period: int = 14
    adx_threshold: float = 25.0


class EmaCrossAdx(DirectionalTradingControllerBase):
    async def update_processed_data(self):
        df = self.market_data_provider.get_candles_df(
            self.config.candles_connector,
            self.config.candles_trading_pair,
            self.config.interval,
            self.config.max_records,
        )

        # 1. Indicators
        df.ta.ema(length=self.config.ema_fast, append=True)
        df.ta.ema(length=self.config.ema_slow, append=True)
        df.ta.adx(length=self.config.adx_period, append=True)

        # 2. Column references
        fast_col = f"EMA_{self.config.ema_fast}"
        slow_col = f"EMA_{self.config.ema_slow}"
        adx_col  = f"ADX_{self.config.adx_period}"

        # 3. Vectorized signal logic
        long_cond  = (df[fast_col] > df[slow_col]) & (df[adx_col] > self.config.adx_threshold)
        short_cond = (df[fast_col] < df[slow_col]) & (df[adx_col] > self.config.adx_threshold)

        df["signal"] = 0
        df.loc[long_cond,  "signal"] = 1
        df.loc[short_cond, "signal"] = -1

        # 4. Export
        self.processed_data["signal"]   = int(df["signal"].iloc[-1])
        self.processed_data["features"] = df
```

## Step 3 — Self-review checklist

Before uploading:

- [ ] **No row loops** — grep for `iterrows`, `apply`, `for idx`; must be zero
- [ ] **All params from config** — no hardcoded numbers in the signal logic
- [ ] **Signal values** — only `1`, `-1`, `0`
- [ ] **Column names** — pandas_ta convention: `EMA_{length}`, `RSI_{length}`,
      `ADX_{length}`, `MACD_{fast}_{slow}_{signal}`, `BBL_{length}_{std}`,
      `BBU_{length}_{std}`, `SUPERTd_{length}_{multiplier}`. Verify against actual
      output columns — a mismatch is a runtime `KeyError`
- [ ] **NaN handling** — indicators emit NaN during warm-up; NaN comparisons yield
      `False`, which correctly maps to `signal = 0`
- [ ] **`signal` populated for all rows** — default 0, then overwritten by masks
- [ ] **`iloc[-1]`, not `iloc[0]`** — the current bar is what the engine acts on
- [ ] **Indicator lookback < `max_records - 50`** — headroom for warm-up
- [ ] **No look-ahead** — `.shift(1)` wherever entry-bar logic needs it
- [ ] **Complete module** — both classes, all imports

## Step 4 — Upload the controller

```python
manage_controllers(
    action="upsert",
    target="controller",
    controller_type="directional_trading",
    controller_name="ema_cross_adx",
    controller_code="<full python source>",
)
```

Confirm `created: true` / `updated: true`. Add `confirm_override=True` when
replacing an existing template.

The `controller_code` string is wrapped as a `Controller` object by the MCP tool —
the endpoint rejects a bare source string.

A new `.py` may still be shadowed by the module cached in `sys.modules` — if the
upload succeeds but `describe` keeps showing the old fields, restart the API
container.

## Step 5 — Create the initial config

```python
manage_controllers(
    action="upsert",
    target="config",
    config_name="ema_cross_adx_v1",
    config_data={
        "controller_type": "directional_trading",
        "controller_name": "ema_cross_adx",
        "connector_name": "binance_perpetual",
        "trading_pair": "BTC-USDT",
        "candles_connector": "binance_perpetual",
        "candles_trading_pair": "BTC-USDT",
        "interval": "1h",
        "max_records": 500,
        # strategy params
        "ema_fast": 20,
        "ema_slow": 50,
        "adx_period": 14,
        "adx_threshold": 25.0,
        # execution params
        "total_amount_quote": 100,
        "stop_loss": 0.03,
        "take_profit": 0.05,
        "trailing_stop": None,
        "time_limit": 86400,
        "leverage": 5,
    },
)
```

Rules that actually bite:

- **Numbers must be numbers, not strings.** `"ema_fast": "5"` reaches the
  backtesting engine as a string and fails on arithmetic.
- **`controller_name` must match the controller module name** (`ema_cross_adx` →
  `bots/controllers/directional_trading/ema_cross_adx.py`). It is how the backend
  resolves the config class.
- `config_name` becomes the YAML filename **and** the config id.
- Config classes set `extra="forbid"` — a typo'd field is a hard rejection, not a
  warning. Discover the real field set with
  `manage_controllers(action="describe", controller_name="<name>")`.
- `confirm_override=True` is required when overwriting an existing config.

## Step 6 — Modifying an existing controller

**Config only:**
1. `manage_controllers(action="describe", config_name="...")` — read current values
2. Re-upsert with `confirm_override=True` and the updated `config_data`

**Controller code:**
1. `manage_controllers(action="describe", controller_name="...", include_code=True)`
2. Rewrite the **full** `update_processed_data` — never patch partial snippets
3. Re-run the self-review checklist
4. Upsert with `confirm_override=True`
5. Re-upload any configs referencing it — they do **not** auto-update

## Go/No-Go → Phase 3

✅ **GO** — controller and config uploaded without errors, checklist fully passes,
code is a complete importable module.

❌ **NO-GO** — any row-level loop, hardcoded magic numbers in the signal logic,
column name mismatch, or `max_records` < 2× the longest indicator length.

## Artifacts

1. Controller source (complete module)
2. Named config with all parameters
3. Self-review checklist results
