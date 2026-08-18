---
name: Backpack Perp MM
description: ''
agent_key: null
skills: []
default_config:
  frequency_sec: 3600
  total_amount_quote: 60000
default_trading_context: ''
created_by: 481175164
created_at: '2026-08-10T21:16:55.078013+00:00'
---

# Backpack Perp MM Strategy

Market making on Backpack perpetuals targeting the MM Rewards Program.
Agent pinned to `local` server.

## Loop playbook

Each tick:

1. **Volume check** — Run `backpack_volume_scan` routine to get fresh 24h volumes.
   Compare current market volumes against previous tick. Flag markets where volume
   dropped below qualification threshold.

2. **Bot health** — Check `manage_bots(action="status")` for the running bot.
   Verify all controllers are in `running` state. Check for errors in logs.

3. **Performance assessment** — For each controller:
   - Current PnL (realized + unrealized)
   - Volume generated vs target (need 1.5% VS per market)
   - Inventory balance (base_pct within min/max band)
   - Fill rate and TP hit rate

4. **Compliance check** — Verify program metrics:
   - Are avg order sizes ≥ $2,000?
   - Are spreads within 100bps?
   - Is uptime acceptable for Liquidity Score?

5. **Adjust if needed**:
   - If a market's volume dropped and qualification is at risk → consider rotating
     to a more tractable market
   - If inventory is drifting → adjust target/min/max_base_pct live
   - If PnL is bleeding → widen spreads or increase cooldown
   - If volume target is not being met → tighten spreads cautiously

6. **Journal** — Write a summary of the tick: volumes, PnL, compliance status,
   any adjustments made.

## Active deployment

- Bot: `backpack_mm_perps_20260811-20260810-211539`
- Controllers (all 3bps L1 / 15bps L2, 6bps TP, 2x leverage, $20K each):
  - `001_pmm_backpack_ZEC-USDC` — ZEC perp
  - `002_pmm_backpack_WLD-USDC` — WLD perp
  - `003_pmm_backpack_PUMP-USDC` — PUMP perp

## Risk limits

- Max $20K per controller ($60K total across 3 markets)
- 2x leverage
- 5% global stop loss per controller
- No global TP — let volume cycle

## Skills to read

- `backpack_perp_mm_deploy` — full config template and deployment steps
- `backpack_volume_scan` routine — fresh volume data
