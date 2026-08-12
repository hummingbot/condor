---
name: orca_pool_selection
description: Interpret the neutral Orca MCDA shortlist, verify live Whirlpool facts, and choose a pool and range or HOLD without turning rank into a command.
when_to_use: Read only when the current selection has close candidates, degraded coverage, native contradiction, an unusual risk/range judgment, or a justified rank override. A clear eligible winner and every committed deployment chain use the Strategy without this skill.
references_routine: scan_orca_pools
source: agent:lp_agent_lite
---

# Orca Pool Selection

Use this playbook after `scan_orca_pools` returns current compact evidence. It
guides judgment; it does not repair missing identity, incomplete required facts,
a technical rejection, or a registry conflict.

Use it only while no committed deployment chain exists. A finalized loop-mode
non-SOL/non-quote selection registers in the same tick, including when this skill
resolved close candidates or justified a rank override. Once that combined step
commits an exact pool, pair, BASE symbol, BASE mint, BASE decimals, allocation,
range thesis, canonical registered symbol, and next phase, later sizing,
preparation, and open ticks resume it without another scan, alternative
comparison, skill read, registry check, or registration call.

Use the returned scan schema and coverage for selection. Its best-effort Condor
report is a human diagnostic copy of sanitized input, output, and trace;
`report_id` and `report_error` do not affect rank, coverage, candidate validity,
or permission to deploy, and report failure never justifies another scan.

## Routine Guide: `scan_orca_pools`

Call it only as
`manage_routines(action="run", name="scan_orca_pools",
agent="lp_agent_lite", config={...})`. Treat the Config below as an exact API:
never rename a key or paraphrase a value. Pass the current configured TVL floor,
candidate limit, and all five configured weights. Normally omit the two
routine-owned request controls so their validated defaults apply.

### Top-Level Config Parameters

<!-- routine-config:scan_orca_pools -->
| Config key | Presence | Exact source and use |
|---|---|---|
| `min_pool_tvl_usd` | required | Current Strategy TVL floor; finite and positive. |
| `candidate_scan_limit` | optional | Pass the current configured bounded candidate count. |
| `mcda_weights` | optional | Pass the complete nested configured object below. |
| `request_size` | optional | Routine-owned transport bound; omit unless current config explicitly supplies it. |
| `timeout_seconds` | optional | Routine-owned request timeout; omit unless current config explicitly supplies it. |
<!-- /routine-config -->

When `mcda_weights` is supplied, it must contain exactly all five keys and their
finite zero-to-one values must sum exactly to one.

### Nested `mcda_weights` Parameters

<!-- routine-config:scan_orca_pools.mcda_weights -->
| Config key | Presence | Exact meaning |
|---|---|---|
| `fee_productivity` | optional | Relative fee generation weight. |
| `recent_activity` | optional | Recent flow and acceleration weight. |
| `price_stability` | optional | Adverse-selection and movement weight. |
| `liquidity_depth` | optional | TVL resilience weight. |
| `execution_simplicity` | optional | Tick, adaptive-fee, and persistence weight. |
<!-- /routine-config -->

Parse the inner JSON, not outer MCP completion. Require its exact `schema`,
`status`, `coverage`, `windows`, `candidates`, and `mutation=false` before using
the shortlist. `complete` means all four bounded discovery lenses returned;
`degraded` means only two or three and requires explicit caution; `unavailable`
provides no deployable shortlist. Read candidate arrays only in the returned
`windows` and MCDA order. Run no second successful scan in the same tick; the
Agent-level pre-execution `Invalid config:` correction rule is the sole allowed
extra call.

Before comparison, parse only exact injected execution-learning records with
`BLACKLIST_POOL=<pool>` and `BLACKLIST_TOKEN=<base_mint>`. Exclude the exact
pool and every candidate with that exact BASE mint before selection. Ignore
malformed records and all general learning prose. A blacklist is persistent
exclusion evidence only; it supplies no price, quality, ownership, or mutation
authority.

Also exclude an exact pool while a current-session LP there is active or may
have landed. At most one active or possibly landed LP per exact pool is
mandatory and has no config waiver. A pool becomes eligible again after its
terminal close and cleanup are resolved, or after its open is proven
`rejected_before_submit` under the corrected-retry contract. A failed-close
quarantined pool remains excluded while its exact on-chain position is active;
after exact closure, reuse also requires fresh wallet feasibility under normal
sizing, reserve, capital, and risk gates.

## Establish Evidence Quality

1. Confirm the scan used current `min_pool_tvl_usd`, `candidate_scan_limit`, and
   the exact five configured `mcda_weights`.
2. Require exact pool address; an exact quote-side match to configured symbol,
   mint, and decimals; exact base mint and decimals; finite positive
   price/TVL/tick spacing; fee metadata; consistent duplicate identity; and
   explicit no-warning evidence.
3. Interpret source coverage honestly:
   - all four discovery lenses: complete source coverage for the four bounded
     requests, not the whole Orca universe;
   - two or three: degraded comparison evidence, not a fabricated complete set;
   - fewer than two: unavailable for deployment selection.
4. A transport-bounded candidate prefix is complete per returned row but not the
   entire eligible universe. Do not invent facts for omitted candidates.

The four discovery lenses are 24-hour fee yield/TVL, 7-day fee yield/TVL,
24-hour volume, and 7-day volume. The normalized evidence covers
1h/4h/24h/7d periods. Do not check Gateway registry presence while selecting.
After final selection, non-SOL/non-quote registration is the same tick's sole
external mutation. Its exact add-and-read-back result completes commitment; an
exact metadata conflict means selection does not complete.

Read the compact candidate schema exactly:

- `base=[symbol,mint,decimals]` and `pool` is the Whirlpool address;
- `volume_usd`, `fees_usd`, and `price_change` follow returned
  `windows=["1h","4h","24h","7d"]` in that order;
- `mcda` follows `[fee_productivity,recent_activity,price_stability,
  liquidity_depth,execution_simplicity]` in configured-weight order;
- `fee_meta=[fee_rate,fee_tier,adaptive]`; do not invent units for a raw fee
  field, and verify current fee mechanics natively;
- `returned` counts included rows and `omitted` counts eligible rows excluded by
  the response bound. Neither count says the four API requests covered all pools.

## Read The MCDA Components

- Fee productivity: prefer repeatable fee generation relative to supplied
  liquidity, not one isolated spike.
- Recent activity: distinguish persistent flow from fading or artificial volume.
- Price stability: favor fee opportunity that is plausible after adverse
  selection and impermanent-loss risk; stability is not a demand for zero
  volatility.
- Liquidity depth: use TVL as execution and resilience evidence while respecting
  the absolute configured floor.
- Execution simplicity: the neutral component combines tick-spacing/adaptive-fee
  mechanics with fee persistence. Wallet inventory, registration tuple support,
  and range feasibility remain separate admission checks; registration executes
  immediately after final non-SOL/non-quote selection.

The configured weights and routine math own the neutral score. Do not rescore in
prose, change weights from session language, or treat rank 1 as an automatic
trade.

## Verify And Compare

For the bounded shortlist, use
`explore_dex_pools(action="get_pool_info", connector="orca",
network=<config.network>, pool_address=<exact pool>)` to confirm current
price, liquidity, fee, token identity, and tick mechanics. Never call
GeckoTerminal or another external market-data fallback. The scan routine is the
primary comparison surface and native Orca pool detail is the verification
surface; if together they cannot support a required comparison or range
judgment, reject only that candidate or `HOLD`.

Compare candidates on:

- sustainability of fees and volume across horizons;
- current native facts versus the official scan;
- price direction, volatility, and whether a useful range is plausible;
- depth and tick spacing relative to the intended small position;
- token identity and a complete supported registration tuple;
- overlap with existing wallet-wide LP exposure;
- source coverage and the cost of being wrong.

Prefer a lower-ranked candidate only when specific current evidence supports a
more sustainable fee opportunity, safer executable range, or materially better
portfolio fit. State the facts supporting the override. Reject only the affected
candidate; one unsuitable pool does not force a global `HOLD` when another is
fully eligible.

## Choose Range Intent

Orca uses ticks and tick spacing. Select a strategic half-width within current
config bounds using verified price, recent realized movement, fee activity, and
the trade-off between concentration and time in range. The deterministic sizing
routine aligns prices and calculates token composition; do not estimate raw
token amounts or tick arithmetic here.

- Narrower ranges concentrate fee earning but exit range sooner and create more
  inventory conversion risk.
- Wider ranges remain active longer but dilute fee concentration.
- In a strong one-directional move, widen defensively, choose another candidate,
  or `HOLD`; high volume alone does not compensate automatically.
- Existing same-base or correlated LP exposure is a reason to prefer
  diversification, not permission to exceed any cap.

Return one selected exact pool plus its scanner `base=[symbol,mint,decimals]`
tuple, a range thesis, and allocation within config, or `HOLD`. Registration,
exact feasible-size math, preparation, execution, and reconciliation remain
with the Strategy.

In loop mode, finalize a non-SOL/non-QUOTE selection only when its complete
registration tuple is ready, then follow the Strategy's same-tick
`SELECT_REGISTER` mutation. Preserve the confirmed canonical symbol, exact
`base_mint`, and `base_decimals` so later ticks resume at `SIZE` without token
rediscovery. SOL and QUOTE selections commit read-only and resume at `SIZE`.
