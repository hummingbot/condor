### Onchain Executor
**This is the way to sign arbitrary EVM transactions from a strategy.** It hands a
set of calls to the Aomi Pipeline, which fork-simulates them, and — when
`commit` is true — signs and broadcasts them from the Aomi wallet, recording
the transaction hashes on the executor.

Two ways to say what to sign:
- **`mode="operation"`** — a builder from the catalog (`app`, `operation`,
  `arguments`): Uniswap V4 swaps, LI.FI swaps/bridges, deBridge orders, Lido and
  ether.fi withdrawal claims, and the Solana swaps (Jupiter, Raydium, Sanctum) with
  `chain="svm"`. Aomi builds the calls; you never touch calldata.
- **`mode="calls"`** — raw EVM calls (`to`, `data`, `value`). This is how every
  other protocol is reached: Aomi's 40+ **skills** (Aave, Morpho, Compound, Curve,
  Pendle, Lido, ether.fi, Rocket Pool, ...) are instructions — contract addresses,
  function signatures and rules — that you read with the `aomi_skill` routine and
  turn into `data.signature` + `data.args` calls. Simulation still gates the commit.

What the catalog will **not** offer you: Aomi's own chat plumbing
(`ask_authorization`, `schedule_cron`, `spawn_thread`, `brave_search`, ...) and its
raw stage/simulate/commit primitives (`evm_stage_tx`, `evm_commit_txs`,
`svm_commit_ix`, ...). Authorization is decided by the wallet's signing policy on
the Aomi side, scheduling is the tick engine's job, and this executor owns the
lifecycle. Passing one of those as `operation` is refused at create time. Reads
(`get_*`, `*_quote`, `*_status`) are listed for `aomi_read`, not as build targets.

**Use when:**
- Executing a DeFi action (swap, lend, stake, bridge) on an EVM chain from an agent
- Sending a native transfer or a contract call that no other executor expresses
- You want a fork simulation before anything is signed

**Avoid when:**
- Trading on a CEX (use order/position/grid/dca executors)
- Providing CLMM liquidity (use `lp_executor`)
- Solana swaps that Hummingbot's own Gateway connectors already cover (Jupiter, Raydium, Orca, Meteora) — prefer the native `lp_executor`/Gateway path there; use this executor for what Gateway lacks

#### Setup Workflow

1. **Browse the catalog** — run the `aomi_catalog` routine: the builders and reads
   of each app, and the protocol skills. For a protocol without a builder, run
   `aomi_skill` (skill="aave") and build `calls` from its contracts and signatures.
2. **Read state first** — run the `aomi_read` routine (`op="account"` for the
   wallet's balance and nonce, `op="token-holdings"` for an ERC-20 balance,
   `op="contract"` to inspect a target, `op="context"` for block and gas). Do not
   sign into a wallet whose balance you have not read.
3. **Decide the mode** — an app operation when the catalog has one; raw calls otherwise.
4. **Declare the bound** — set `notional_quote` to the value at risk in quote
   terms. **Required for agents**: the risk gate values the create with it (plus
   any native `value` the calls carry, priced through the CEX feed, plus
   `max_gas_quote`). A create with no bound is refused.
5. **Create** — `manage_executors(action="create", executor_type="onchain_executor", executor_config={...})`.
6. **Read the result** — poll the executor (`action="search"`, `executor_id=...`)
   until `status == "TERMINATED"`, then read `close_type` and `custom_info`.

#### Key Parameters

| Parameter | Required | Meaning |
|---|---|---|
| `chain_id` | yes | EVM chain: `8453` Base, `1` Ethereum, `42161` Arbitrum, `10` Optimism |
| `chain` | no | `evm` (default) or `svm` for Solana operations in `mode=operation` |
| `skills` | no | Skills to load alongside the app for a build (rarely needed; skills are read with `aomi_skill`) |
| `mode` | yes | `calls` (raw `evm_stage_tx` calls) or `operation` (an app operation) |
| `calls[]` | `mode=calls` | Each `{to, description, data: {signature, args, raw}, value}` — `value` is a **wei string** (`"0"` for none); `data.signature` + `data.args` for an ABI call, or `data.raw` for pre-encoded calldata |
| `app` / `operation` / `arguments` | `mode=operation` | The catalog entry to execute and its argument map (see `aomi_catalog`) |
| `notional_quote` | agents: yes | Value at risk in quote terms; the risk gate values the create with it |
| `max_gas_quote` | no | Gas ceiling in quote terms; added to the valuation |
| `commit` | no | Default `true`. `false` = simulate only — nothing is signed |
| `controller_id` | yes | Your session's agent id; attributes the executor to you |

#### Example: a 0-value self-transfer on Base

The smallest real transaction — it proves the wallet, the chain and the commit
path without moving funds. `WALLET` is the Aomi wallet's own address (read it
with `aomi_read(op="account")`).

```python
manage_executors(
    action="create",
    executor_type="onchain_executor",
    executor_config={
        "controller_id": "<your agent id>",
        "chain_id": 8453,
        "mode": "calls",
        "calls": [
            {
                "to": WALLET,
                "description": "self-transfer smoke test",
                "data": {"signature": "", "args": [], "raw": ""},
                "value": "0",
            }
        ],
        "notional_quote": 1,     # nothing at risk beyond gas; declare a token bound
        "max_gas_quote": 1,
        "commit": True,
    },
)
```

#### Reading the Result

- `custom_info.tx_hashes` — the broadcast transaction hashes (empty when
  `commit=false` or the executor failed before signing)
- `custom_info.simulation_passed` — whether the fork simulation passed
- `custom_info.error` — `{reason, message, code}` when the executor failed; the
  `reason` is the one to act on
- `custom_info.wallet_address` — the wallet Aomi signed from
- `close_type`: `COMPLETED` (committed, hashes recorded), `FAILED` (simulation or
  commit failed — see `custom_info.error`), `EARLY_STOP` (stopped before commit)

#### Important

- **Stop is a no-op after commit.** An on-chain transaction cannot be recalled;
  `manage_executors(action="stop")` only matters while the executor is still
  simulating. Simulate first (`commit=false`) when unsure.
- **Precondition:** the Hummingbot API process needs `AOMI_URL` and `AOMI_TOKEN`,
  and the Aomi wallet must be in **`server_auto`** signing mode for an unattended
  commit. Otherwise the executor ends `FAILED` with `reason: awaiting_wallet`
  — the transaction was staged but nobody signed it.
- One executor, one atomic set of calls: split unrelated actions into separate
  executors so a failure is attributable.
