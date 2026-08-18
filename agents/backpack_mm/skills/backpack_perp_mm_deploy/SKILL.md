---
name: backpack_perp_mm_deploy
description: End-to-end playbook for deploying pmm_mister bots on Backpack perps optimized
  for the MM Rewards Program — adapted aggressive config, volume targets, and program
  compliance.
when_to_use: When deploying, configuring, or tuning market making bots on Backpack
  perpetuals. Also when asked about Backpack MM program compliance, order sizing,
  or spread settings for the rewards program.
created: '2026-08-10T21:14:30Z'
source: agent:backpack_mm
---

# Backpack Perp MM Deploy Playbook

Adapted from the market_making_expert's aggressive pmm_mister profile,
tuned specifically for the **Backpack MM Rewards Program** on perpetuals.

## Program constraints (hard requirements)

- **Avg order size ≥ $2,000** — orders below this don't count
- **Orders within 100bps from mid** — spreads must be < 0.01
- **Volume Score ≥ 1.5%** per market to receive rewards
- **Post-only orders** — must be LIMIT_MAKER (type 3) for maker volume
- **Uptime matters** — Liquidity Score = (Avg Order Size / Avg Spread) × Uptime

## Config template — Backpack MM Aggressive

Fetch the full config from the companion file:
```
manage_skill(action="read_file", name="backpack_perp_mm_deploy", file="config_backpack_aggressive.md")
```

### Key adaptations vs standard aggressive profile

| Parameter | Standard Aggressive | Backpack MM | Why |
|-----------|-------------------|-------------|-----|
| total_amount_quote | $500 | **$20,000** | Min $2K orders need larger capital |
| levels | 2 equal | **2 weighted (2:1)** | Front-load L1 for volume, L2 for depth/Liquidity Score |
| spreads | 8bps, 15bps | **6bps, 15bps** | Tighter L1 for fills, wide L2 for depth |
| effectivization_time | 60s | **300s** | More time for TP to fill on thin markets → volume cycling |
| take_profit | 8bps | **6bps** | Fast cycle, viable with MM5 fees (first month) |
| cooldown_time | 30s | **30s** | Prevent adverse stacking on thin markets |
| leverage | 10x | **2x** | Mid-tier perps are volatile, protect capital |
| order_type | 3 | **3** | LIMIT_MAKER mandatory for program credit |

## Deployment steps

1. **Run volume scan** — `manage_routines(action="run", name="backpack_volume_scan")`
   to get fresh 24h volume data and identify tractable markets.

2. **Pick markets** — Select 3-5 mid-tier perps where needed maker volume for
   1.5% VS is achievable ($8-15K/day range). Avoid BTC (needs $1M+/day).

3. **Verify pairs exist on Backpack perps** — Before creating any config, confirm
   each ticker actually exists as a perpetual on Backpack by checking
   `GET https://api.backpack.exchange/api/v1/markets`. The volume scan may report
   spot-style tickers that don't match the actual perp symbol. Known gotchas:
   - BONK perp = **kBONK**, SHIB perp = **kSHIB** (kilo-denominated)
   - TRX, STABLE — **no perp exists**
   - When in doubt, fetch the markets list and grep for the symbol

4. **Read config template** — fetch `config_backpack_aggressive.md` companion file.

5. **Adapt per market** — substitute `trading_pair`, verify leverage is within
   the market's max (check `imfFunction.base` from /api/v1/markets).

6. **Set position mode** — Backpack perpetual **only supports ONEWAY** position
   mode. Always set `"position_mode": "ONEWAY"`. Never use `HEDGE` — the bot
   will error on startup if HEDGE is set.

7. **Upsert configs** — one config per market via `manage_controllers(action="upsert", target="config", ...)`.
   Name convention: `NNN_pmm_backpack_<PAIR>`.

8. **Deploy single bot** with all controllers:
   ```
   manage_bots(action="deploy", bot_name="backpack_mm_perps_<timestamp>",
     controllers_config=["config1", "config2", "config3"])
   ```

9. **Verify** — `manage_bots(action="status")` to confirm all controllers running.

## Volume math (per controller)

- Order size L1: $20K × 0.25 × (2/3) = **$3,333** ✓ (above $2K)
- Order size L2: $20K × 0.25 × (1/3) = **$1,667** (depth, may not count for avg)
- Per complete fill→TP cycle: 2 × $3,333 = **$6,666** maker volume
- Need ~2-3 cycles/day per market for 1.5% VS on $900K market
- At 300s effectivization + 30s cooldown: achievable

## Monitoring

- Schedule `backpack_volume_scan` daily to track volume shifts
- Check bot PnL and fill rates via mm_bot_report
- Watch for markets dropping below threshold — may need to rotate
