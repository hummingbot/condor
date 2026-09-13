# Market Making Fly — implementation design

Status: **implemented (see §18); this document is the reference.**
Date: 2026-09-12

## 1. Summary

Market Making Fly is a new Condor agent modeled on Market Making Expert, but the
discretionary part of market making — *what regime are we in, how wide should
we quote, which way should the reference price lean* — is not decided by an LLM
or by indicator thresholds. It is decoded from the spiking activity of a
simulation of the **MaleCNS v1.0 fly connectome** (166,700 neurons, 25.6 M
directed connections) that is shown a rendered **OHLCV candlestick chart** of the
market, exactly the way [stonkfly](https://github.com/nftechie/stonkfly) shows
a fly a line chart of BTC-USDC and reads buy/sell/hold out of its descending
neurons.

The strategy P&L is fed back into the fly the same way stonkfly does it: a
positive change pulses the 15 **PAM11** reward dopamine cells, a negative change
pulses the 2 **PPL101** aversive dopamine cells, and the candidate memory rule
adjusts the 7,835 existing **KC → MBON07 / MBON11** synapses.

Everything that touches money stays deterministic and outside the fly, in the
stonkfly spirit: the connectome **proposes** a quoting posture, a fixed mapping
turns the posture into a `pmm_mister` config, a **guard can veto but never
substitute**, and Hummingbot's controller executes. The Condor LLM agent is the
*operator* — it picks the market, deploys the bot, starts and stops the fly,
reports, and explains — it never overrides the fly's posture with its own view.

The trading universe is **Hyperliquid HIP-3 perps (xyz issuer)** on
`hyperliquid_perpetual`, reusing Market Making Expert's HIP-3 scanner and its
HIP-3 operating rules.

FLM (the "Fly Language Model") was the first candidate and is **not** used:
it is a frozen 1.2 B-parameter chat model whose next-token scores get a small
correction from the connectome, and its own paper reports the fly readout does
not beat a matched non-fly control. Stonkfly's direct spiking-simulation route
puts the actual connectome in the decision path, which is what you asked for.

## 2. What is reused from stonkfly, what is replaced

| Stonkfly piece | MM Fly | Notes |
|---|---|---|
| `neural/` package: graph import, C++ LIF kernel, `MemoryBrain`, `VisualMemoryBrain`, `circuit` (PAM11/PPL101/KC/MBON), `rule` (anti-Hebbian memory), checksum locks | **Vendored unchanged** into `agents/market_making_fly/flybrain/neural/` | MIT. Only the data-directory constant changes. Keeping it byte-identical keeps its lock/provenance chain valid. |
| `data.py` prepare / verify (1.1 GB MaleCNS download, checksum, compile `graph.npz`) | Vendored, exposed as the `fly_setup` routine (`action=prepare|verify|bench`) and `python agents/market_making_fly/flybrain/__main__.py …` | Data lives in the agent's own git-ignored home. |
| `display.market_frame` (320×180 line chart) | **Replaced** by `agents/market_making_fly/flybrain/chart.py`: OHLCV candlesticks + volume | Same canvas size and light palette so the retinal projection is unchanged. |
| `Decoder` (DNp20 R−L → BUY/SELL/HOLD) | **Replaced** by `agents/market_making_fly/flybrain/decoder.py`: DNp20 R−L → trend/skew, a population-rate channel → arousal/spread, gate on DNpe017 | Still a fixed, engineered readout of spike counts. |
| `reinforcement.py` (equity delta vs anchor, deadband) | Same rule, equity = controller `realized + unrealized − fees` | Filtered by the bot/controller, so other bots on the account do not leak in. |
| `risk.Guard` / `Veto` | `agents/market_making_fly/flybrain/guard.py`: fee floor, loss stop, loss-rate breaker, apply cooldown, market-open, collateral, price-move tolerance | Veto keeps the previous config. It never invents a different posture. |
| `broker.py`, `actions.py`, `ledger.py` (Coinbase FOK orders, SQLite intents) | **Dropped** | Execution is Hummingbot `pmm_mister`; the fly never places orders itself. |
| `cli.py` run loop, 2-slot checkpoints, `events.jsonl`, `latest.json`, `latest-input.png`, provenance hash | Re-implemented as the `fly_brain` continuous routine + `agents/market_making_fly/flybrain/run_state.py` | Same durability pattern. |

## 3. Pipeline

```
 every interval_sec (default 60 s)
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ 1. fetch  : last N candles (HIP-3 pair) + live l2Book bid/ask              │
 │ 2. render : chart.py → 320×180 RGB uint8 (OHLCV + volume + bid/ask)        │
 │ 3. reward : Δ(controller net P&L) vs last observation → reward|aversive|none│
 │ 4. observe: worker process — VisualMemoryBrain.rgb_step for neural_ms      │
 │             (default 500 ms neural time), dopamine pulse if reward/aversive │
 │ 5. decode : spike counts → {trend_z, arousal_z, gate} → regime, spread_mult,│
 │             shift_bps (posture)                                            │
 │ 6. map    : posture → pmm_mister config (HIP-3 base config × posture)       │
 │ 7. guard  : vetoes (fee floor, loss stop, cooldown, market closed, …)       │
 │ 8. apply  : if live mode and posture changed materially → update_config    │
 │ 9. persist: checkpoint (2 slots), events.jsonl, latest.json, PNG, LiveReport│
 └────────────────────────────────────────────────────────────────────────────┘
                              ▲                                   │
      Condor LLM agent (operator) ─ deploy / stop / report / explain ┘
```

Ordering follows stonkfly: reinforcement for the *previous* observation is
delivered at the start of the *next* neural window; the checkpoint and
accounting anchor are committed **before** anything is applied to the bot.

## 4. What the fly sees — chart specification

`agents/market_making_fly/flybrain/chart.py: market_frame(pair, candles, bid, ask) -> np.uint8[180,320,3]`

* Canvas 320×180, light background `(235,240,249)`, dark header bar with the
  pair name — identical to stonkfly. Stonkfly found the dark chart produced no
  Kenyon-cell spikes and switched to light; we keep that.
* **Candles**: last `n_candles = 72` of `interval = 5m` (6 h window) drawn 4 px
  wide in the 294 px plot span. Up candle body **blue `(0,101,183)`**, down
  body **red `(197,37,78)`**, 1 px wicks in the body color. Blue/red is
  stonkfly's palette: the mapped R8p cells read the blue channel and R8y the
  green channel, R1–R6 read luminance, so colour is not decoration here — it is
  the only chromatic input the fly gets.
* **Volume**: bars in a bottom strip (rows 140–158), grey-blue, scaled to the
  window maximum.
* **Bid / ask**: two short horizontal ticks at the right edge at the bid and ask
  price, plus stonkfly's `BID x ASK y` footer text.
* Price axis: window `[min low, max high]` padded 12 % top and bottom, with a
  floor of 0.2 % of mean price so a flat market does not blow up to full scale
  (stonkfly's rule).
* **Not drawn**: the bot's own quotes, inventory, P&L, funding, or anything
  from the account. Stonkfly deliberately excludes portfolio state from the
  picture; P&L reaches the fly only through dopamine.

The frame is saved as `latest-input.png` every observation, so you can see what
the fly saw when it made a call. `fly_chart` is also a one-shot routine, so you
can render the chart for any pair from chat and inspect it.

Candles come from Hummingbot (`client.market_data.get_candles_last_days` on
`hyperliquid_perpetual`), which already serves HIP-3 pairs. The live book comes
from Hyperliquid `l2Book` directly with the `xyz:TOKEN` coin form (the
hummingbot-api `order_book` endpoint 500s on HIP-3 — documented in the HIP-3
operator playbook).

## 5. The neural substrate

* `agents/market_making_fly/flybrain/neural/` = stonkfly's `neural/` package plus `data.py`, vendored
  with a `THIRD_PARTY.md` notice (MIT). Files: `brain.py`, `state.py`,
  `circuit.py`, `rule.py`, `visual.py`, `sensory.py`, `transmitters.py`,
  `connectome.py`, `prepare.py`, `kernel.cpp`, and the four `*.lock.json`
  checksum files. The only edit is `common.DATA`, which resolves to Condor's
  data dir instead of `./data`.
* Integration parameters stay stonkfly's: 0.1 ms timestep, 500 ms neural time
  per observation in 10 ms bins, 200 ms / 20 mV dopamine pulse, KC rest −60 mV
  with adaptation, R8 → aMe12 sign correction, memory rule gain 0.001, 1 s
  eligibility traces, 1,800 s memory decay, efficacy bounds 0.1–2×.
* **Data**: `fly_setup` (`action=prepare`) downloads ~1.1 GB (annotations + edges
  feather), verifies SHA-256 against the locks, compiles `graph.npz`
  (166,700 × 25,582,938) and builds the C++ kernel with `c++ -O3`. Location:
  `.condor/agents/market_making_fly/data/` (the agent's writable home, git-ignored),
  overridable with `CONDOR_FLY_DATA`. `fly_setup` `action=verify` re-checks.
* **Process model**: the simulation is CPU-bound C++ called through ctypes and
  must not run on Condor's event loop. The `fly_brain` routine owns a
  `ProcessPoolExecutor(max_workers=1, spawn)` whose initializer loads
  `VisualMemoryBrain` once; each tick submits `observe(frame, reinforcement)`
  and awaits the future. Frames are 172 KB, results are small dicts.
  Checkpoint/restore run in the same worker. One worker per running fly
  instance; a per-run lock file prevents two instances on one run directory
  (stonkfly's `worker.lock`).
* **Compute budget**: stonkfly does not publish a per-observation timing.
  `fly_setup` `action=bench` will run three observations on a synthetic frame and print
  `compute_seconds`; if it exceeds ~40 % of `interval_sec`, lower `neural_ms`
  (it is a config field). Memory: the graph is ~300 MB resident, checkpoints
  ~100 MB each × 2 slots.

## 6. Decoder — spikes → quoting posture

`agents/market_making_fly/flybrain/decoder.py`. Engineered and fixed, like stonkfly's — it reads only
spike counts, and the cell IDs it reads are written to the audit log.

Raw channels per observation (`seconds = neural_ms / 1000`):

| Channel | Cells | Measurement |
|---|---|---|
| `trend_hz` | DNp20 left vs right (stonkfly's BUY/SELL cells) | mean right rate − mean left rate |
| `arousal_hz` | descending-neuron population (`superclass == "descending"` in `graph.npz`, minus the DNp20/DNpe017 readouts) | mean firing rate |
| `gate` | DNpe017 | spike count ≥ 1 |

Baseline-centering (new, addresses a problem stonkfly documented): stonkfly's
six-observation run proposed BUY every time — a persistent turning bias of the
circuit became persistent buying. In market making that would become a
persistent skew. So the decoder keeps a running mean/std of `trend_hz` and
`arousal_hz` over the last `baseline_window = 60` observations (persisted in
`state.json`) and works in z-scores. The first `warmup = 10` observations emit
the neutral posture while the baseline forms. `center_bias` is a config flag
(default on) so a raw, uncentred run is still possible as a control.

Regime, in precedence order:

| Regime | Rule |
|---|---|
| `pause` | `arousal_z ≥ 2.5` |
| `volatile` | `arousal_z ≥ 1.0` |
| `trending_up` | `gate` and `trend_z ≥ 1.0` |
| `trending_down` | `gate` and `trend_z ≤ −1.0` |
| `quiet` | `arousal_z ≤ −1.0` |
| `ranging` | otherwise, and during warm-up |

Continuous outputs:

* `spread_mult = clip(1 + 0.5 · arousal_z, 0.6, 2.5)` — the fly agitated by a
  jumpy chart widens, a calm fly tightens.
* `shift_bps = clip(1.0 · trend_z, −max_shift_bps, +max_shift_bps)` with
  `max_shift_bps = 3` and a hard cap of 50 % of the first-level spread — the
  reference price leans with the fly's turning direction.

Hysteresis so the bot is not re-configured on noise: a new posture is only
applied if the regime changed, or `|Δspread_mult| ≥ 0.15`, or
`|Δshift_bps| ≥ 0.5`, and at least `min_apply_interval_sec = 300` have passed
since the last apply. Otherwise the tick is `HOLD`. The fly is still observed and
reinforced every tick; only the application is rate-limited (stonkfly's order
cooldown, transposed).

Thresholds (1.0 / 2.5 z, 0.5 gain, 3 bp) are declared model choices with no
fitted basis. They are config fields.

## 7. Posture → `pmm_mister` config

`agents/market_making_fly/flybrain/posture.py: build_config(base, posture, fees) -> dict`. The base
config is the HIP-3 operator's bounded defaults, the posture multiplies and
shifts it:

* Base spreads from the scanner's picked spread `S` bp: level 1
  `max(2, S/2)`, level 2 `S+1` (HIP-3 playbook). Then
  `buy = base · spread_mult − shift`, `sell = base · spread_mult + shift`,
  each floored at `min_spread_bps = 3`.
* `take_profit = max(0.0004, level-1 buy spread)` — and never below
  `2.2 × round-trip maker fee` (HIP-3 all-in ~1.3 bp/side → 2.6 bp; floor 5.7 bp
  wins). Market Making Expert's rule "TP must exceed round-trip fees" becomes a
  hard floor in code rather than advice.
* Timing per regime, from Market Making Expert's table: quiet
  `refresh 20 / cooldown 30`, ranging `30 / 60`, trending `30 / 60`, volatile
  `60 / 120`.
* Fixed by the HIP-3 playbook, not touched by the fly: `target_base_pct 0.4,
  min 0.3, max 0.5`, `portfolio_allocation 0.2`, `max_active_executors_by_level
  2`, `leverage ≤ 5` and ≤ market max, `open/take_profit_order_type 3`
  (LIMIT_MAKER), `global_sl_enabled true, global_stop_loss 0.02`, uppercase
  `XYZ:TOKEN-USD` pair.
* `pause` regime → `manual_kill_switch = true` (quotes pulled, position kept);
  leaving `pause` → `false`.
* Optional (off by default): nudge `target_base_pct` by ±0.05 in a trending
  regime. Off because the HIP-3 rules say tight bands are what stopped the SPCX
  and DRAM losses.

Applied through `manage_bots(action="update_config", …, confirm_override=true)`
on the live bot **and** `manage_controllers(action="upsert")` on the saved
config, both layers, per the deploy playbook.

## 8. Guard — vetoes only

`agents/market_making_fly/flybrain/guard.py`. Every rule is deterministic; a veto means "keep the
previous config", a halt means "stop the bot and stop the fly until a human
passes `resume_reviewed=true`". None of them chooses a different posture.

| Check | Effect |
|---|---|
| Market closed (live book missing a side) — HIP-3 equities close off-hours | veto (HOLD); after `closed_ticks_to_stop` consecutive closed ticks, STOP bot, leave fly observing |
| Available USD on the unified Hyperliquid account < required margin | veto |
| Any spread `< min_spread_bps`, TP below fee floor, leverage above cap | veto (should be unreachable after `posture.py` floors; this is the belt to those braces) |
| Apply cooldown (`min_apply_interval_sec`) or daily apply cap (`max_applies_per_day = 48`) | veto |
| Mid moved more than `apply_price_tolerance = 0.5 %` between observation and apply | veto (stonkfly's fresh-book check) |
| `total_net ≤ −max_loss_quote` (default 4 % of `total_amount_quote`) | **halt**: STOP bot, position closed by the controller's `global_stop_loss` / market-close, fly halted |
| HIP-3 loss-rate breaker: `total_net / volume ≤ −5 bp` or no new session high for 25 ticks | **halt** with market-close, exactly the HIP-3 playbook's mandatory rule |
| `update_config` failed 3 consecutive ticks | **halt** for review |
| STOP file in the run directory | halt (stonkfly's `touch runs/live/STOP`) |

`total_net` always includes unrealized P&L — the HIP-3 playbook's rule.

## 9. Reinforcement — P&L → dopamine

`agents/market_making_fly/flybrain/reinforcement.py`, stonkfly's function with a different equity
source:

* `equity_t = realized_pnl + unrealized_pnl − fees` for **this bot's**
  `pmm_mister` controller, read from `manage_bots(action="status")`
  performance (the same fields `mm_dashboard` reads), so nothing else on the
  account can reward or punish the fly.
* `delta = equity_t − anchor` where `anchor` is the previous observation's
  equity. `delta ≥ +deadband` → `reward` (PAM11 ×15), `≤ −deadband` →
  `aversive` (PPL101 ×2), else `none`. Deadband default = 1 bp of
  `total_amount_quote` (stonkfly used 0.01 on $100, the same 1 bp).
* Binary pulse, 200 ms, 20 mV-equivalent, delivered during the first 200 ms of
  the next 500 ms window; not proportional to the amount; tiny changes are not
  accumulated. All as stonkfly.
* The memory rule and plastic edge set are stonkfly's, untouched: 7,835
  KC→MBON07/MBON11 edges, baseline-centred anti-Hebbian rule fed by actual
  KC and DAN spike rates.
* `learning = true` by default; `frozen = true` freezes efficacies for a
  control run. In **shadow** mode with no bot deployed, reinforcement is `none`
  every tick (no P&L exists); with a bot deployed under the fly's name it is
  the bot's P&L even though the fly's postures are not being applied — stated
  plainly in the status report because that signal has nothing to do with the
  fly's own proposals.

What this is and is not: it is feedback about the controller's marked value
between two observations, including fees; it is not evidence that the last
posture caused it. Stonkfly's validation found plastic edges changing *before*
the first external pulse — endogenous dopamine activity drives the rule too. We
report `changed_edges`, `mean_efficacy`, `reward_spikes`, `aversive_spikes`
every tick and make no claim beyond them.

## 10. Trading universe — HIP-3

* Connector `hyperliquid_perpetual`, issuer `xyz`, pairs `XYZ:TOKEN-USD`
  (uppercase; lowercase → KeyError → zero orders).
* Market selection is the existing `hip3_market_scanner` routine (copied into
  the agent): volume, spread-vs-fee, daily drift, `l2Book` depth filter,
  `TOP PICK`. Selection is the operator's job (deterministic routine + LLM
  reading it), not the fly's — the fly never chooses which market it is shown,
  as in stonkfly's fixed round-robin.
* Venue facts carried over from the HIP-3 operator: unified collateral (read
  from `get_portfolio_overview(["hyperliquid_perpetual"])`, not the per-dex
  clearinghouse state), isolated margin, trading hours / empty books, ~1.3
  bp/side all-in maker fee including the fixed Hummingbot builder fee, tight
  inventory bands, `global_stop_loss 0.02`.
* **Three markets, one brain (decided 2026-09-12).** The fly quotes three
  HIP-3 markets in parallel and learning is shared: a single
  `VisualMemoryBrain` is shown the three charts in a fixed round-robin
  (stonkfly's multi-asset schedule — the network never chooses which market it
  sees), one pair per tick, so each pair is observed every `3 × interval_sec`.
  Reinforcement is the change in the **combined** net P&L of the three
  controllers since the previous observation, delivered while whichever chart
  is up — feedback about the fly's whole book, as stonkfly's is about the whole
  portfolio. Baselines and postures are **per pair** (keyed by pair in
  `state.json`); the KC→MBON weights are shared and persist in one checkpoint
  lineage. Rotating a market swaps the pair in the list and resets that pair's
  baseline only; the brain and its learned efficacies carry on.
* Rotation: only when flat and the current market is closed, trending against
  us, or dominated — re-run the scanner and redeploy that slot.

## 11. Condor integration — files

```
agents/market_making_fly/
  flybrain/                 # everything below lives inside the agent; nothing in condor/ changes
  __init__.py
  neural/                 # vendored stonkfly.neural + THIRD_PARTY.md (MIT)
  data.py                 # vendored prepare/verify, Condor data dir
  chart.py                # OHLCV → 320×180 RGB
  decoder.py              # counts → channels, z-scores, regime, posture
  posture.py              # posture → pmm_mister config with floors
  guard.py                # vetoes / halts
  reinforcement.py        # equity delta → reward|aversive|none
  worker.py               # ProcessPoolExecutor target: load brain, observe, checkpoint, restore
  run_state.py            # run dir: state.json, events.jsonl, latest.json, PNG, 2-slot checkpoints, provenance, lock, STOP
  __main__.py             # prepare | verify | bench, runnable by path

agents/market_making_fly/
  AGENT.md                # operator brain (LLM)
  routines/
    fly_chart.py          # one-shot: render + report the frame for a pair
    fly_brain.py          # CONTINUOUS: the loop in §3; modes shadow|live, learning|frozen, fixture|market
    fly_status.py         # one-shot: latest posture, channels, z-scores, spikes, memory stats, P&L, vetoes, halt reason
    hip3_market_scanner.py  # copied from Market Making Expert
    mm_dashboard.py         # copied
  skills/
    fly_mm_deploy/SKILL.md      # deploy playbook (adapted from pmm_mister_deploy: scanner → base config → deploy → start fly_brain)
    fly_decoder/SKILL.md        # how to read fly_status and the decoder
    pmm_config_playbook/        # copied
    capital_allocation/         # copied
    mm_bot_report/              # copied
  strategies/fly_hip3_operator/strategy.md   # thin loop: keep bot + fly alive, surface halts, rotate when flat

agents/market_making_fly/tests/     # conftest puts the agent dir on sys.path
  test_fly_chart.py test_fly_decoder.py test_fly_posture.py
  test_fly_guard.py test_fly_reinforcement.py
  test_fly_full_graph.py   # opt-in, CONDOR_FLY_FULL_TEST=1, needs prepared data

docs/market_making_fly_design.md   # this file, kept as the reference
```

Run state per fly instance:
`.condor/agents/market_making_fly/fly/<run_name>/` with `state.json`
(baseline stats, anchor, tick, last applied posture/config, halt reason),
`events.jsonl` (one row per tick: quote, frame hash, spike summary, channels,
posture, config diff, guard result), `latest.json`, `latest-input.png`,
`brain-0.npz` / `brain-1.npz`, `provenance.json` (settings, dataset hashes,
circuit report, decoder description, source hashes — a changed protocol refuses
to resume into an old run dir, as stonkfly does), `worker.lock`, optional `STOP`.

### The agent (LLM) — what it does and does not do

`AGENT.md` keeps Market Making Expert's domain knowledge (regimes, spread
calibration, inventory, fee rule, `pmm_mister` parameter guide) so it can
*explain* what the fly did in market-making terms, and adds one rule above all
others:

> While a fly run is live on a pair, the fly quotes and you operate. You deploy,
> start, stop, rotate, and report. You never set spreads, skew, or regime from
> your own analysis, and you never "correct" the fly's posture. If you believe
> the fly is wrong, you stop it and say why; you do not out-vote it.

Two modes, like Market Making Expert:

* **Consulted**: "what does the fly see on DRAM right now", "why did it widen",
  "is it learning anything" → runs `fly_status` (and `fly_chart`), answers in
  key: value lines, quotes the numbers, repeats the caveats in §15.
* **Delegated / loop**: `fly_mm_deploy` skill end-to-end: scanner → TOP PICK →
  base config from `pmm_config_playbook` balanced profile adapted with HIP-3
  bounds → deploy with `max_global_drawdown_quote` → start `fly_brain`
  (shadow first unless told live) → verify with `mm_bot_report`. The loop
  strategy ticks every 5 min: confirms bot and fly instance alive, surfaces
  halts/vetoes, rotates when flat and the market is closed.

Tools list = Market Making Expert's plus `manage_routines` for the fly
routines. `server_name` left empty, `agent_key` copied from Market Making
Expert.

### Modes

| Mode | Observes | Applies config | Reinforcement | Use |
|---|---|---|---|---|
| `shadow` (default) | real chart | no — logs what it *would* apply | bot P&L if a bot is deployed, else none | first runs, watch the posture stream against `market_analyzer` |
| `live` | real chart | yes | bot P&L | after shadow looks sane |
| `frozen` flag | any | any | pulses delivered, weights frozen | control run |
| `fixture` flag | synthetic sine candles | never | none | offline plumbing test, `fast=true` skips wall waits |

## 12. Deploy lifecycle

1. `fly_setup` with `action=prepare` once per install (1.1 GB, several minutes, needs `c++`).
2. Operator agent runs the scanner, deploys `pmm_mister` on the TOP PICK with
   the neutral (ranging) posture config.
3. `manage_routines(action="start", name="fly_brain", agent="market_making_fly",
   config={"trading_pair": "XYZ:DRAM-USD", "bot_name": "dram-mm",
   "config_name": "dram_mm_live", "mode": "shadow", "run_name": "dram-2026-09-12"})`.
4. Watch `fly_status` / the LiveReport for an hour; compare the fly's regime
   stream with `market_analyzer` for the same window (report only).
5. Restart with `mode: "live"` on the same run directory (baseline and memory
   carry over).
6. Halts require `resume_reviewed: true` on restart, and a financial halt cannot
   be cleared that way at all (stonkfly's rule) — a new run directory is needed.

## 13. Observability

* `fly_brain` maintains one `LiveReport`: latest chart PNG, regime timeline
  (last 100 ticks), `trend_z` / `arousal_z` sparklines, spike KPIs, memory
  KPIs (`changed_edges`, `mean_efficacy`), P&L and reinforcement history,
  applied-config history, vetoes/halts.
* `fly_status` gives the same as text for consults and the Telegram loop.
* The agent journals every apply and halt with the posture and the guard
  verdict. `events.jsonl` is the audit trail; each row carries the frame hash,
  spike-count hash, and checkpoint hash so a decision can be tied to exactly
  what the fly saw and what state it was in.

## 14. Testing and validation plan

Unit (no data, run in CI):

* `chart.py`: deterministic frame for fixed candles (hash), shape/dtype, up
  candle pixels are blue and down red, bid/ask ticks land at the right rows,
  flat-market scale floor.
* `decoder.py`: synthetic counts → expected regime for each row of the table;
  warm-up emits ranging; centring removes a constant bias; hysteresis blocks
  small changes and passes regime changes.
* `posture.py`: fee floor beats spread; shift never pushes a side below
  `min_spread_bps`; pause sets the kill switch; timing table per regime; pair
  is uppercase.
* `guard.py`: every row of the table in §8, halt vs veto, `resume_reviewed`
  semantics, financial halt not clearable.
* `reinforcement.py`: stonkfly's four cases.
* `run_state.py`: checkpoint slot alternation, provenance mismatch refuses
  resume, lock file.

Opt-in with prepared data (`CONDOR_FLY_FULL_TEST=1`): stonkfly's full-graph
test transposed — a white frame activates KCs, reward and aversive pulses spike
their DAN cells, eligible edges change, frozen stays identical, checkpoint
round-trips; plus a rendered real chart produces non-zero KC activity (this is
the check stonkfly says is the biggest open modelling question).

Runs, in order: `fixture + fast + steps=6` offline; `shadow` on a live HIP-3
pair for at least 60 observations (one baseline window); `live` at
`total_amount_quote = 100` USD; only then normal size.

Not planned in this pass, and required before any learning claim: held-out
chronological replay, shuffled-reinforcement control, exposure/cash baselines,
retention after reset. The design leaves room for them (`frozen`, fixture
market, run directories) but does not deliver them.

## 15. What this does and does not claim

Copied in spirit from stonkfly's `docs/model.md`, because the same limits hold:

* The connectome supplies anatomy; the LIF cells, transmitter sign proxies,
  RGB-to-photoreceptor mapping, compressed clock (500 ms neural per 60 s wall)
  and dopamine assignments are engineered choices, not fly physiology.
* The decoder is an interface we chose. "Trend from DNp20, arousal from
  descending neurons" is not a discovery of market-making neurons. A persistent
  circuit bias becomes a persistent skew; baseline-centring reduces but does
  not remove that.
* Candle colours were chosen to hit the R8 channels; that is a display adapter.
* P&L pulses are feedback about the controller's value, not credit assignment.
  Weight changes do not show the fly learned to quote.
* No profitable learning is demonstrated by anything in this design. The guard
  and the controller's stop-loss are what bound the loss, not the fly.

## 16. Decisions

Settled 2026-09-12: 1 vendor, 2 deterministic apply, 3 descending neurons,
4 baseline-centred, 5 72 × 5 m, 6 net P&L incl. unrealized, 7 **three markets
in parallel on one shared brain** (see §10), 8 shadow default, 9
`.condor/agents/market_making_fly/data`, 10 no target-base nudge. The original options are kept
below for the record.

1. **Vendor stonkfly's neural package into `agents/market_making_fly/flybrain/neural/` (recommended)**
   vs `pip install git+…stonkfly`. Installing pulls `coinbase-agentkit` and
   `coinbase-advanced-py` into Condor for no use; vendoring adds ~1,400 lines +
   the kernel + `pyarrow` as a new dependency.
2. **Deterministic apply (recommended)** — the `fly_brain` routine applies the
   config itself and the LLM only operates — vs the LLM loop reading the
   posture and applying it each tick. The second puts an LLM back between the
   fly and the bot, which is what stonkfly's "no LLM trading policy" rule
   avoids, and costs tokens every minute.
3. **Arousal population = descending neurons (recommended)** vs whole-network
   mean rate vs Kenyon cells. All three are engineered; descending neurons are
   the motor-output side stonkfly already reads from and are labelled in
   `graph.npz` without the annotations file.
4. **Baseline-centre the channels (recommended, flag)** vs raw rates. Raw is
   stonkfly's choice and is the honest one for an experiment; centred is the
   one that will not sit skewed one way for hours because of circuit bias.
5. **Chart window 72 × 5 m (recommended)** vs 100 × 1 m (stonkfly's density)
   vs 1 h candles. 5 m matches the regime cadence the config timing table
   assumes; 1 m is what the fly could realistically react to at 60 s ticks.
6. **Reinforcement source = controller net P&L incl. unrealized, 1 bp deadband
   (recommended)** vs realized-only. The HIP-3 rules insist on unrealized.
7. **Fresh run directory per market (recommended)** vs carrying learned
   weights across markets.
8. **Default mode shadow (recommended)**; live is explicit.
9. **Data dir `<local root>/fly/data` (recommended)** vs `~/.condor/fly`.
10. Whether the target-base nudge in trending regimes (§7, off by default)
    should exist at all.

## 17. Implementation phases

| Phase | Deliverable | Verifies |
|---|---|---|
| 1 | `agents/market_making_fly/flybrain/neural` vendored, `data.py`, `fly_setup` routine, `pyarrow` dep | `prepare` completes on this Mac, `verify` passes, `bench` prints compute time |
| 2 | `chart.py` + tests, `fly_chart` routine | rendered DRAM chart in a report |
| 3 | `decoder.py`, `posture.py`, `guard.py`, `reinforcement.py` + tests | unit suite green |
| 4 | `worker.py`, `run_state.py`, `fly_brain` routine, fixture mode | `fixture + fast + steps=6` run end-to-end with checkpoint resume |
| 5 | `AGENT.md`, skills, strategy, `fly_status`, copies of scanner/dashboard | consult works; shadow run on a live HIP-3 pair |
| 6 | live mode at small size | your call, after reviewing shadow output |

Rough effort: phases 1–4 are the bulk; 5 is mostly adaptation of Market Making
Expert text; 6 is operation, not code.

## 18. Implementation status (2026-09-12)

Phases 1–5 are implemented; phase 6 (live at small size) is the operator's call.

| Piece | Where | State |
|---|---|---|
| Vendored neural package, data prepare/verify, kernel | `agents/market_making_fly/flybrain/neural/`, `flybrain/data.py`, `fly_setup` routine | done; dataset prepared at `.condor/agents/market_making_fly/data` (1.6 GB), verified, kernel built |
| Chart, decoder, posture, guard, reinforcement, naming, market, run state, worker | `agents/market_making_fly/flybrain/*.py` | done, 77 unit tests green (`uv run pytest agents/market_making_fly/tests`) |
| Loop routine | `agents/market_making_fly/routines/fly_brain.py` | done; 14-observation fixture run end to end (checkpoints, events, live report, halt path) |
| `fly_chart`, `fly_status`, copied scanner/dashboard | `agents/market_making_fly/routines/` | done, load through routine discovery |
| Agent brain, deploy playbook, decoder skill, operator strategy | `agents/market_making_fly/{AGENT.md,skills,strategies}` | done |
| Opt-in full-graph test | `agents/market_making_fly/tests/test_fly_full_graph.py` (`CONDOR_FLY_FULL_TEST=1`) | done |
| Doctor row | — | not done; use `fly_setup` `action=verify` |

Deviations from the text above: the descending-neuron superclass label in
`graph.npz` is `descending_neuron` (1,314 cells); the whole implementation is
contained in `agents/market_making_fly/` (package `flybrain`, put on `sys.path`
by the routines) so no core Condor module changes — the only repo-level edit is
the `pyarrow` dependency; setup is the `fly_setup` routine rather than a CLI;
checkpoints are ~7 MB compressed, not 100 MB. Condor re-executes a routine
file when it changes but keeps imported modules cached, so after editing
anything under `flybrain/` restart Condor before starting `fly_brain`.

### First measurements

* Bench: brain loads in ~1 s; one 500 ms neural window costs 2–5 s of compute on
  this Mac. A 60 s wall interval leaves ample room.
* DNp20 fires 26–46 Hz on a chart with a consistent right-minus-left surplus of
  +2 to +12 Hz in every observation — stonkfly's persistent bias, now measured.
  Baseline-centring is what keeps it from becoming a permanent upward lean.
* A rendered chart activates 10–17 Kenyon cells per window at first (stonkfly's
  chart gave 11–16). An aversive pulse produced 13–30 PPL101 spikes, a reward
  pulse ~270 PAM11 spikes, and 5 KC→MBON edges changed from endogenous activity
  before any pulse, as stonkfly also reported.
* In the fixture run the network flipped, at the ninth observation, into a
  persistent high-activity state (total spikes 388 k → 609 k per window, KC
  spikes 12 → 4,000+, descending rate 2.5 → 8 Hz, 2,900 plastic edges
  changed). A control run with reinforcement disabled reproduced the flip at
  the same tick with the same numbers, so it is driven by the visual input
  sequence, not by the dopamine pulses. It is what the arousal channel is meant
  to read, but it means "volatile" can persist until the rolling baseline
  absorbs the new level (up to one window). Stonkfly's warning that display
  sensitivity is the largest open modelling question applies here in full.

## 19. Housekeeping from this session

* FLM was cloned, installed, and its downloads started at `~/flm` (4.3 GB)
  before the change of direction. The install was stopped. Delete with
  `rm -rf ~/flm ~/flm-setup.sh ~/flm-setup.log` when you like; nothing in this
  design uses it.
* The scaffold copy `agents/market_making_fly/` from the first attempt was
  removed; the tree is clean apart from this document.
