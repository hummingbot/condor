# Validation

What was verified before this agent was proposed, how, and what remains unverified.

## Running the tests

```bash
uv run pytest tests/test_bollinger_band_trader.py -v
```

58 tests, ~1.7s, no network and no Hummingbot server required. The routines are loaded by
file path exactly the way `discover_routines_from_path` loads agent-local routines at
runtime, and driven against a fake client with synthetic candles. `ReportBuilder` is
stubbed so the suite never writes into `reports/`.

The full repo suite was run alongside it: **286 passed, 5 skipped**.

## What is covered

**Band math** — the middle band equals the SMA, the outer bands are `mid ± 2σ` using
*population* standard deviation (the Bollinger definition; the test asserts the sample
deviation would give a different answer, so a swap cannot slip through), %B and bandwidth
match their formulas, a zero-variance series does not divide by zero, and the percentile
rank handles empty history, the extremes, and ties at half weight.

**Indicator helpers** — SMA/EMA series shapes and seeding, and true range using the
previous close across a gap.

**ADX** — high on a pure trend, below the trend threshold in a bounded range, bounded to
0–100, returns 0 rather than raising on short input, and never saturates.

**Classification** — one engineered fixture per regime (coil, firing coil, steady grind,
bounded range at each band), plus a sweep asserting that **every** verdict is reachable on
realistic data. A branch that never fires is dead code, and the sweep is what catches it.

**Setup derivation** — each setup's entry, stop and target are checked against the band
level they are supposed to come from (breakout stops at the middle band and targets 2R;
the band walk enters at the mean and targets the opposite band; the reversion enters at
the band and targets the mean). All three rejection gates are tested: the
higher-timeframe veto in both directions, the below-average-volume breakout filter, and
the R:R floor.

**A 2,400-case fuzz** runs every regime fixture against every trend verdict and asserts
that no emitted setup ever has an inverted stop or target. This is the check that matters
most — an inverted stop is an instant loss, not a bad trade.

**Sizing** — the notional is derived from the stop distance so risk is constant, the
position cap and the reserve each bind correctly, and a capped size is surfaced as a WARN
rather than silently reducing risk. Every guardrail is tested for the FAIL it should
produce, and a failing check is asserted to emit **no** executor payload.

**Payload shape** — `side` maps 1/2, `stop_loss` and `take_profit` are decimal fractions
rather than prices, and the connector/pair fields are present. This is checked against the
schema in `mcp_servers/hummingbot_api/guides/position_executor.md`.

**Degradation** — no candles, a connector that raises, a watchlist where nothing has
enough history, an unknown portfolio value, and an invalid `side` all produce a clean
message instead of an exception or a silently wrong number.

**Round trip** — `band_state`'s levels are parsed out of its own summary and fed into
`band_trade_sizer`, asserting the two routines agree on R:R. The handoff between them is
the seam most likely to rot.

## Bugs the tests found

Three defects were found by testing and fixed before this PR. Each now has a named
regression test.

**1. A firing squeeze reported `squeeze_pending`.** The squeeze check ran before the
expansion check. Bandwidth rank is computed from history, so on the exact candle a coil
breaks, the rank is still low — the routine classified the breakout bar as a squeeze and
told the agent to stand aside on the one bar the entire setup exists to trade. Fixed by
testing expansion first. → `test_firing_coil_reads_as_expansion_not_squeeze`

**2. `reversion_range` was unreachable.** ADX started as a raw single-window DX, which
saturates near 100 on any sustained directional push. Reaching a Bollinger band *is* a
directional push, so every band tag read as a trend. Measured: across 600 synthetic
ranges, a band tag never once produced a reversion verdict. Replaced with Wilder's
smoothed ADX, after which the setup fires normally. → `test_adx_does_not_pin_at_100_in_a_range`

**3. A capped position size rendered as PASS.** When the position cap or the reserve
reduced the size, the check row showed PASS, so the operator had no signal that the trade
was risking less than the configured target. Now emitted as an explicit WARN row. →
`test_capped_size_is_reported_as_a_warn_row`

A fourth, smaller issue surfaced while calibrating: the band-walk threshold was `%B ≥ 0.95`,
which a smooth trend never reaches — it rides at 0.90–0.93. Relaxed to the top decile and
gated on ADX > 25 so a range cannot be mistaken for a walk.

## What is NOT validated

- **No live exchange run.** Every test uses synthetic candles through a fake client. The
  routines have not been executed against a real Hummingbot API, so connector-specific
  response shapes (candle key names, funding payloads) are handled defensively but
  unproven. Run `band_state` manually against a real server before launching a loop.
- **No profitability claim.** Nothing here is a backtest. The tests prove the agent
  computes what it says it computes and refuses what it says it refuses — not that the
  setups make money. `run_backtest` against a controller would be the next step, and the
  thresholds (`squeeze_rank`, the R:R floors, the ADX split) should be treated as
  starting points to be tuned per market.
- **No executor was created.** `manage_executors` is never called in the tests; only the
  payload shape is checked against the documented schema. The first real deploy should be
  a `dry_run` session, as the strategy's own instructions require.
- **Synthetic fixtures are not market data.** A reflecting random walk is a reasonable
  stand-in for a range and an exponential curve for a trend, but neither reproduces real
  microstructure, gaps, or volume behaviour. The reachability sweep says the branches can
  fire, not how often they will fire live.
