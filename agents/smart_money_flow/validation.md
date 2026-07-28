# Live-Run Validation Runbook

Goal: prove the Smart-Money Flow agent trades correctly on Derive **before** any
real capital — and before the PR is submitted. The signal + decision layer is
already unit-verified (`routines/onchain_flow.py` scores live data correctly);
this runbook covers the **execution** path that only a live (or testnet) bot can.

## Why testnet first
The Condor/API layer **does not add exchange keys** — by design
(`mcp_servers/hummingbot_api/server.py`: *"Connecting/removing exchange API
keys is intentionally NOT exposed here"*). You connect the exchange **once** in
the Hummingbot client, then Condor drives that already-connected instance. So the
"API vs CLI" question is a false either/or: CLI for the one-time key setup,
Condor/API for everything after.

## Step 1 — Connect Derive (one-time, in Hummingbot client)
```
connect derive_perpetual
# prompts: wallet address, private key, subaccount id, account type
connect            # verify "Keys Added / Confirmed"
```
For validation, use the testnet connector instead:
```
connect derive_perpetual_testnet
```
(Your build's `conf_client.yml` already lists `derive: {}` / `derive_testnet: {}`,
so the connector is present — no rebuild needed.)

## Step 2 — Point Condor at the running bot
Configure the Hummingbot API server connection (the Condor side that IS exposed):
```
# via the humbingbot_api MCP server's configure tool, or your server config
server url = http://<hbot-host>:8000   # default Hummingbot API port
```
Verify with a read call (no keys touched):
```
portfolio()   # should show the connected derive_perpetual[_testnet] balances
```

## Step 3 — Dry / testnet run
1. Set `default_trading_context` connector to `derive_perpetual_testnet` (tiny size).
2. Launch the agent loop (`manage_routines` + the loop strategy) on the testnet.
3. Watch for, per tick:
   - `onchain_flow` routine returns a verdict + dashboard (no exceptions).
   - On a `LONG`/`SHORT` verdict, a `PositionExecutor` opens on the right pair.
   - Risk Engine enforces limits (max 2 positions, 3x lev, 8% DD).
   - TP/SL/trail logic fires; position closes; journal entry written.
4. Force both directions at least once (the routine's `synthesize` can be fed a
   synthetic RISK-ON/+flow and RISK-OFF/−flow to exercise LONG and SHORT without
   waiting for the market).

## Step 4 — Mainnet (only after Step 3 is clean)
- Switch connector back to `derive_perpetual`, fund the wallet, start with a
  small `total_amount_quote`, and monitor the same checks on real capital.

## Step 5 — Pre-PR evidence
Attach to the PR: a short log/screenshot of the testnet run showing
- routine verdict + dashboard,
- at least one opened + closed position with correct sizing/limits,
- zero Risk-Engine violations.

This is the "ensure it properly works before submitting PR" gate.
