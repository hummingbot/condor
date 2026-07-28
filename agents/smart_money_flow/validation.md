# Live-Run Validation Runbook

Goal: prove the Smart-Money Flow agent trades correctly on Derive **before** any
real capital is at risk — and before the PR is submitted. The signal + decision
layer is already unit-verified (`routines/onchain_flow.py` scores live data
correctly); this runbook covers the **execution** path that only a live bot can.

## Important Condor constraints (verified in code)
- **API keys are added ONLY via the web dashboard** (Settings → Keys). The
  Telegram `/keys` command is **read-only** (it lists connected exchanges and
  deep-links to the web UI). The Condor/API (MCP) layer does not add keys.
- **Perpetual TESTNET connectors are NOT available in Condor.** `condor/web/
  routes/settings.py` filters every connector whose name contains "testnet"
  (and "sandbox", and "/"). So `derive_perpetual_testnet`,
  `binance_perpetual_testnet`, etc. are not selectable in the UI — only
  mainnet connectors (`derive_perpetual`, `binance_perpetual`, …) appear.
  (Hummingbot core HAS them in `conf_client.yml`, but Condor's web layer hides
  them.) Therefore validation runs on **mainnet Derive with a tiny, isolated
  wallet** — not on a testnet.

## Step 1 — Connect Derive (web dashboard only)
1. Open the Condor web dashboard → **Settings → Keys**.
2. Add a connector: select **`derive_perpetual`** (mainnet).
   Prompts: wallet address, private key, subaccount id, account type.
3. (Telegram alternative: `/keys` shows the 📈 perpetual entry once connected —
   it cannot add keys itself.)
Use a **dedicated, minimally-funded wallet** — never your main holdings.

## Step 2 — Point Condor at the running bot
Configure the Hummingbot API server connection (the Condor side that IS
exposed) with the bot's URL (default `http://<hbot-host>:8000`). Verify with a
read call (no keys touched): `portfolio()` should show the connected
`derive_perpetual` balances.

## Step 3 — Tiny-size mainnet validation
1. Set `default_trading_context` connector to `derive_perpetual`, and start with
   a small `total_amount_quote` (e.g. 50–100 USDC) on the isolated wallet.
2. Launch the agent loop (`manage_routines` + the loop strategy).
3. Watch for, per tick:
   - `onchain_flow` routine returns a verdict + dashboard (no exceptions).
   - On a `LONG`/`SHORT` verdict, a `PositionExecutor` opens on the right pair.
   - Risk Engine enforces limits (max 2 positions, 3x lev, 8% DD).
   - TP/SL/trail logic fires; position closes; journal entry written.
4. Force both directions at least once: feed `synthesize()` a synthetic
   RISK-ON/+flow and RISK-OFF/−flow to exercise LONG and SHORT without waiting
   for the market.

## Step 4 — Scale only after Step 3 is clean
Once one LONG and one SHORT have opened and closed correctly with correct
sizing/limits and zero Risk-Engine violations, raise `total_amount_quote`.

## Step 5 — Pre-PR evidence
Attach to the PR: a short log/screenshot of the validation run showing
- routine verdict + dashboard,
- at least one opened + closed position with correct sizing/limits,
- zero Risk-Engine violations.

## Note on testnet
If a true testnet gate is desired, it requires a Condor change: relax the
`"testnet" not in c.lower()` filter in `condor/web/routes/settings.py` for
validation connectors (or add a per-user "allow testnet" flag). Out of scope
for this agent PR unless you want to propose it.

## Operational note — Derive connector init (learned during live test)
The `hummingbot-api` container's `derive_perpetual` connector builds its
symbol map from Derive's live API on startup. If a bad pair is attempted
(e.g. `SOL-USDT` instead of the correct `SOL-USDC`), the order-book websocket
subscription throws `KeyError` in
`derive_perpetual_api_order_book_data_source.py` and **every subsequent
`create_executor` hangs** (no order placed, funds untouched) until the
connector re-initializes.

**Fix that worked:** `docker restart hummingbot-api` forces a fresh connector
init against Derive's API, repopulating the symbol map so `SOL-USDC` resolves.
After that, orders place normally.

**Golden rules for Derive:**
- Always use `SOL-USDC` / `ETH-USDC` / `BTC-USDC` (Derive perps are USDC-quoted;
  `-USDT` does not exist and poisons the connector state).
- If `create_executor` hangs with a `KeyError` in the Derive data source,
  restart the `hummingbot-api` container before retrying.
- Live test result (2026-07-28): LONG `SOL-USDC` placed + closed cleanly on
  Derive mainnet, ~$0.02 fees, funds returned. Full lifecycle verified.
