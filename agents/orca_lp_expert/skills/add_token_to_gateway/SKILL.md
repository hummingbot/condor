---
name: add_token_to_gateway
description: Playbook for adding a missing token to the Hummingbot Gateway so it can
  be used in Orca LP positions. Covers both the UI walkthrough and the two input formats.
when_to_use: When the user wants to LP or trade a token on Orca but the token is not
  in the Gateway token list — Gateway refuses the pair, the executor fails with "token
  not found", or the pool browser cannot resolve the token symbol. Common for newly-launched
  RWA tokens (SPCX, SILV, GME, etc.) that are not pre-loaded.
created: '2026-08-14T17:00:52Z'
source: agent:orca_lp_expert
---

# Add a Token to the Gateway

Use this playbook whenever a token is not recognized by the Gateway — the pool exists on Orca, but the executor or swap UI cannot resolve the token symbol or mint address.

---

## 1. Confirm the token is really missing

Before adding, verify the pool exists and find the exact mint address:

- Run the pool scanner (`orca_rwa_pool_analysis`) or search on Orca UI.
- Note the **mint address** of the missing token (base58, ~44 characters on Solana).
- Note the **network** — for Orca it is always `solana-mainnet-beta` (the Gateway network ID for Solana mainnet).

---

## 2. Navigate to Gateway → Tokens

In Telegram (or the Condor web dashboard):

```
/gateway  →  🪙 Tokens  →  Select network  →  ➕ Add Token
```

> If the network list shows only "default" networks and Solana is not there, tap **🌐 All Networks** to expand it.

---

## 3. Add the token — two input formats

The bot prompts for input after tapping ➕ Add Token. You have two options:

### Option A — Address only (recommended for RWAs)
Paste **only the mint address**. The bot fetches symbol, decimals, and name automatically from GeckoTerminal.

```
9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump
```

This works as long as the token is indexed on GeckoTerminal for the Solana network. Most traded RWA tokens (SPCX, SILV, GME, etc.) are indexed. If GeckoTerminal lookup fails, fall back to Option B.

### Option B — Full format
Send a comma-separated string when auto-lookup fails or you want to override the metadata:

```
address,symbol,decimals,name
```

Example:
```
9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump,SPCX,6,SpaceX Tokenized Equity
```

Field notes:
- `address`: Solana mint address (base58)
- `symbol`: Short ticker as shown on Orca (must match the pool's token symbol)
- `decimals`: Token precision — **6** for most SPL stablecoins and RWA tokens, **9** for native SOL-like tokens. Check on-chain or GeckoTerminal if unsure.
- `name`: Optional — can be omitted (`address,symbol,decimals`)

---

## 4. Verify the token was added

After success, the bot shows `✅ Token Added Successfully` and returns to the token list for that network. The new token should appear in the grid.

To confirm it is now usable:
- Go to the pool browser (`/lp` or Dex page) and search for the pair (e.g., `SPCX-USDC`).
- If the pair resolves and shows a pool address, the token is live in Gateway.

---

## 5. Edit or remove a token

If the metadata was wrong (wrong symbol, wrong decimals):

```
/gateway  →  🪙 Tokens  →  Select network  →  [tap the token symbol]  →  ✏️ Edit
```

Send the corrected values in `symbol,decimals,name` format (address stays the same — Gateway does a delete + re-add internally).

To remove: tap **🗑 Remove** and confirm.

---

## 6. Network ID reference (Orca = Solana)

| Network | Gateway ID |
|---------|-----------|
| Solana Mainnet | `solana-mainnet-beta` |
| Ethereum | `ethereum-mainnet` |
| Arbitrum | `arbitrum-mainnet` |
| Base | `base-mainnet` |
| Polygon | `polygon-mainnet` |

For Orca CLMM LP, always use `solana-mainnet-beta`.

---

## 7. RWA-specific notes

- Most tokenized equity / RWA tokens on Solana use **6 decimals** — verify before adding.
- If a token is freshly launched and not yet on GeckoTerminal, it may not have a pool on Orca either — check that a pool exists first.
- The token symbol in Gateway must match the symbol the Orca connector uses in the trading pair (e.g., `SPCX-USDC` → symbol must be `SPCX`). A mismatch will cause the executor to fail even after adding.
- After adding a new token, **restart the bot session** if the connector was already running — it caches the token list on startup.
  Gateway itself needs no restart: it reads the token list off disk on every request, so the token is live there the moment it is added. It is only the connector's startup copy that is stale.
