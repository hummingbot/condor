# Claude Instructions for Condor / Hummingbot-Condor

This repo (and its sibling `SpicySOB/Hummingbot-Condor`, the patched Hummingbot fork)
are a deliberately customized, debugged, and upgraded version of stock Hummingbot +
Condor, built specifically for this account's Hyperliquid trading. The customizations
are not cosmetic — WS order/cancel reliability, position-tracking reconciliation, price
quantization, controller PnL accounting — are load-bearing fixes for known upstream
bugs that will silently corrupt trading if bypassed. **If a bot isn't running our
version of these tools, it will not work correctly**, and worse, it will often look
like it's working correctly while it isn't. Treat any deviation from the rules below
as the most likely explanation for a confusing bug before looking anywhere else.

## Read this file at the start of every session touching Condor or these bots

If a session's working directory isn't already inside `/home/nate/condor`, still read
this file explicitly before doing any bot deploy, stop, or trading-infra work — don't
rely on directory-based auto-loading alone.

## Deploy discipline

- **Never call hummingbot-api's raw endpoints directly** (`/bot-orchestration/deploy-v2-controllers`,
  `/bot-orchestration/stop-and-archive-bot`, etc. via curl or a bare client call) for
  anything touching a real bot. Always go through Condor's own wrappers:
  `handlers/bots/_shared.py::deploy_v2_controllers_headless()` or
  `mcp_servers/hummingbot_api/tools/controllers.py::deploy_bot()` for deploys,
  `mcp_servers/hummingbot_api/tools/bot_management.py::manage_bot_execution()` for
  stop/start. These wrappers are what actually apply the correct image, reconcile
  positions, and (as of 2026-07-31) cancel orders safely on stop — the raw endpoints
  do none of that by default.
- **Current canonical image**: `condor/hummingbot:hyperliquid-cancel-fix` (verify via
  `docker images | grep hummingbot` and cross-check the tag against what's actually
  running: `docker inspect <container> --format '{{.Config.Image}}'`). Condor's deploy
  wrappers already default to this — the risk is only when bypassing them.
- Every deploy/redeploy **must** go through `reconcile_initial_positions()`
  (`handlers/bots/_shared.py`) so a controller's `initial_positions` starts from the
  real exchange position, not a stale or zeroed value. This already happens
  automatically inside the wrappers above — do not skip it by constructing a deploy
  payload manually.
- After stopping a bot, do not trust that it left no resting orders — see "Verifying
  order/position state" below.

## Keeping the image and controller code in sync with the fork

Hummingbot connector/controller patches live in `SpicySOB/Hummingbot-Condor`, branch
`condor-main` (this is that repo's own default branch, push straight to `origin`
there). Two live delivery paths must both stay in sync with it, and they don't sync
themselves:

1. **`condor/hummingbot:hyperliquid-cancel-fix`** — the connector-level patches baked
   into the image (`hyperliquid_perpetual_derivative.py`, `_auth.py`, `_constants.py`,
   `_user_stream_data_source.py`, `position_executor.py`, `data_types.py`).
2. **hummingbot-api's live shared controllers volume**
   (`/hummingbot-api/bots/controllers/generic/` inside the
   `hummingbot-api-u8vruveh9d2qpt7s4n6hy7qe` container) — bind-mounted as
   `/home/hummingbot/controllers` into **every** bot container. This mount takes
   precedence over anything baked into the image at that same path, so controller
   code (e.g. `pmm_mister.py`) effectively **must** be synced here directly; baking it
   into the image alone does nothing at runtime.

**Run `build_condor_image.sh` (at the root of the `Hummingbot-Condor` fork) any time
that repo advances.** It rebuilds the image from every patched file the fork tracks
AND re-syncs the live controllers volume in the same run. This script is the only
supported way to ship a fork change into the live system — do not hand-copy
individual files with `docker cp`/`docker build` again. (That ad hoc pattern is
exactly how `pmm_mister.py` sat two days stale relative to already-committed fixes on
2026-07-29 → 07-31, causing a serious live-capital incident — see
[[condor_pmm_mister_drift_incident]].)

If you patch something live for an emergency fix, the emergency fix is not done until
it's committed to the fork and `build_condor_image.sh` has been run to make it the
new baseline — a live-only patch will vanish the next time a container restarts.

## GitHub mirror rule

Any change to Condor or Hummingbot-Condor must be committed **and pushed**, never left
as local-only commits.
- Condor app changes → `/home/nate/condor`, push to remote `fork`
  (`SpicySOB/condor`), not `origin` (`hummingbot/condor`, upstream, read-only for this
  user). `fork/main` is a deliberately-diverged personal mirror force-pushed to match
  this local checkout's real history — expected, not a mistake.
- Hummingbot connector/controller changes → clone/push
  `https://github.com/SpicySOB/Hummingbot-Condor.git`, branch `condor-main`, remote
  `origin` there (no separate fork needed, `origin` already points at the user's own
  repo). No durable local checkout — re-clone each session if needed.
- `~/.git-credentials` has a working personal access token; no `gh` CLI installed.

## Verifying order/position state — do not trust these

- `/trading/orders/active` (hummingbot-api) — **broken**, reliably returns empty/wrong
  regardless of real state. Always verify open orders directly against Hyperliquid:
  `POST https://api.hyperliquid.xyz/info {"type":"frontendOpenOrders","user":"<address>"}`.
- `/executors/search` and the `executors` Postgres table — **broken**, permanently
  empty. Don't rely on either for executor-level forensics; use `close_type_counts`
  from `/bot-orchestration/status` for aggregate counts, or raw fills for ground truth.
- `positions_summary` / `global_pnl_quote` (from `/bot-orchestration/status` or the
  Bot Runs tab) — **do not trust for `pmm_mister`**. It only accumulates from
  executors that close via `TAKE_PROFIT`/`EARLY_STOP`/`FAILED`; anything that folds
  via `CloseType.POSITION_HOLD` (the vast majority of `pmm_mister` fills, by design)
  is silently excluded from both realized PnL and volume. This is a real hummingbot
  core gap (`executor_orchestrator.py::_update_cached_performance`), not a display bug.
- `current_base_pct` / `unrealized_pnl_pct` in `custom_info` — reconciled against the
  real exchange position as of the 2026-07-31 `pmm_mister.py` sync (reads
  `connector.account_positions` directly). Before that sync this was stale/wrong; if
  a bot is ever running an older `pmm_mister.py`, treat these as unreliable too and
  re-run `build_condor_image.sh`.
- **Trust hierarchy for anything PnL/position related, most to least reliable:**
  1. Hyperliquid's own API queried directly (`clearinghouseState`, `userFillsByTime`,
     `frontendOpenOrders`) — ground truth, always.
  2. `custom_info.position_amount` from a bot running the current (post-2026-07-31)
     `pmm_mister.py` — reads the real exchange position directly.
  3. Nothing else. Don't average, don't split the difference, don't treat the
     dashboard as a second opinion worth weighing against #1.

## Order cancellation safety

`skip_order_cancellation` defaults to `True` (i.e. **skip** cancellation) in both the
raw hummingbot-api endpoint and the `hummingbot_api_client` library's
`stop_and_archive_bot()` — an easy trap since the name reads like a safe no-op.
Condor's `manage_bot_execution(action="stop_bot")` now explicitly passes `False`
(fixed 2026-07-31) — if calling the client or API more directly for any reason, always
pass `skip_order_cancellation=False` explicitly. Even with that, **do not assume a
stop actually cancelled everything** — it has repeatedly left real resting orders
behind in practice, independent of the flag. After any stop, verify against
`frontendOpenOrders` directly and clean up anything left resting before redeploying
(see [[condor_pmm_mister_drift_incident]] for the working cancel-via-decrypted-creds
approach when hummingbot-api's own cancel endpoint can't see an order it didn't place
itself).

## Rate limits

Hyperliquid grants request-weight budget **per IP/account**, not per bot process.
Each Hummingbot connector's own throttler (`rate_limits_share_pct`, default 100.0)
assumes it alone gets the full budget — running N processes from one host/account
without dividing this trivially blows the real shared ceiling even when each process
is individually well-behaved. This is a **client-level config** (`conf_client.yml`),
not tied to the image — **it resets to 100.0 on every fresh deploy** and must be
re-applied every time (edit `conf_client.yml` inside the container, then
`docker restart`, not a full redeploy, to avoid re-triggering the order-cancellation
risk above). With N bot processes sharing the account, budget roughly `100/N` each
(e.g. 3 processes → ~33 each) and adjust down further if 429s persist.

## pmm_mister config defaults to always set explicitly

Never deploy `pmm_mister` on silent defaults for these — they combine dangerously:
- `global_sl_enabled` / `global_tp_enabled` default to **`False`** even if
  `global_stop_loss`/`global_take_profit` values are set — the portfolio-level
  backstop is configured but inert unless these are explicitly `true`.
- `position_profit_protection` defaults to **`True`**, which can freeze the bot into
  one-directional accumulation once price dips slightly underwater (structurally
  cannot rebalance until price recovers). Set `false` unless deliberately wanted.
- `min_skew` defaults to **`1.0`**, disabling inventory-based order-size tapering
  entirely (every fill full-size regardless of proximity to target). Set ~0.3–0.4.
- `take_profit` should be sized relative to actual round-trip fees (~3bps observed)
  and the pair's natural volatility over the `position_effectivization_time` window
  (default 120s) — a `take_profit` far wider than what's reachable in that window
  means most fills fold into the aggregate position instead of closing cleanly.

## Deep-dive references

For full incident writeups behind the rules above, see memory:
[[condor_pmm_mister_drift_incident]], [[pmm_mister_controller_gotchas]],
[[hyperliquid_ws_duplicate_order_bug]], [[hyperliquid_connector_price_bug]],
[[hummingbot_api_deploy_quirks]], [[github_mirror_rule]].
