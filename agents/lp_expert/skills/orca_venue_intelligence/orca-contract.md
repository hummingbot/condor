# Orca Venue Contract

## Scan contract

`orca_pool_scan` fetches the configured union of Orca category and sorting
lenses, normalizes each record, rejects technical invalidity, deduplicates by
pool identity, derives metrics, and ranks the valid universe. It never selects a
pool, allocation, range, or next action. Every run also saves an inspectable
`Orca LP Pool Scan` report and returns `report_id`; `report_error` is diagnostic
and never changes scan status.

Deployment requires:

- `status = complete`
- `deployable = true`
- all required source requests complete
- one returned candidate that remains valid under fresh detail

Each recommendation supplies identity and venue facts plus:

- raw TVL, fees, volume, and price change across `1h`, `4h`, `24h`, and `7d`;
- fee yield, hourly fee yield, sustainable hourly yield, acceleration, and
  turnover;
- neutral MCDA score, weights, and components;
- neutral rank for comparison, with risk posture applied later by the LLM;
- categorical descriptions such as liquidity, volatility, fee persistence,
  complexity, confidence, and recommendation band;
- source categories, lenses, and count.

These are decision evidence. Category labels are not independent token-security
certification, and rank one is not compulsory.

## Token and price orientation

The current Orca Strategy admits pools with non-USDC token A and canonical USDC
token B. Normalized price is:

```text
token B units per token A unit
```

Verify address, mint, symbol, and decimals rather than trusting a display pair.
A reversed or contradictory orientation invalidates planning.

## Whirlpool range mechanics

Orca Whirlpools use ticks. A pool's `tick_spacing` determines which ticks may be
initializable; price bounds must be translated and aligned to valid ticks. Tick
arrays constrain which tick regions are represented and may require
initialization or traversal by the provider.

The economic range decision and technical validation are separate:

1. The LLM chooses desired width using evidence and configured limits.
2. The Orca planner converts price targets to aligned lower/upper ticks.
3. The current provider/schema validates the resulting position and limit
   prices.

Use runtime planner/provider validation as the authority for maximum feasible
range. Do not encode a remembered global maximum: feasibility depends on the
current price, tick spacing, numeric tick limits, token decimals, tick-array
coverage, and installed connector behavior.

## Difference from bin-based venues

Do not reuse these concepts across venues:

| Orca Whirlpool | Bin-based venue such as Meteora DLMM |
|---|---|
| tick and `tick_spacing` | active bin and `bin_step` |
| tick-aligned bounds | bin-aligned bounds |
| tick arrays | bin arrays |
| Whirlpool price math | DLMM bin price math |
| no Meteora position strategy type | venue-specific strategy types may exist |

Shared LP skills decide pool quality, portfolio role, range intent, inventory,
supervision, and recovery. This venue skill supplies only Orca data semantics and
technical feasibility.

## Upstream references

- [Orca Whirlpools](https://github.com/orca-so/whirlpools)
- [Gateway Orca CLMM connector](https://github.com/hummingbot/gateway/tree/main/src/connectors/orca)
- [Gateway concentrated-liquidity schema](https://github.com/hummingbot/gateway/blob/main/src/schemas/clmm-schema.ts)
- [Hummingbot LP executor implementation](https://github.com/hummingbot/hummingbot/tree/master/hummingbot/strategy_v2/executors/lp_executor)
