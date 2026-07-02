---
name: hyperliquid_tokenized_perps
description: On hyperliquid_perpetual, tokenized perp contracts (equities/pre-IPO names like SPCX-USD) are issued by a provider and require an issuer prefix in the trading pair (e.g. XYZ:SPCX-USD, not SPCX-USD).
when_to_use: A user asks to trade / quote / deploy on a tokenized perp on hyperliquid_perpetual and the plain pair can't be found or looks unavailable — typically equity or pre-IPO names (e.g. SPCX, and other stock-like tickers) rather than crypto majors. Triggers — "trade SPCX on hyperliquid", "can't find <TICKER>-USD on hyperliquid", "is <stock> available on hyperliquid perp"; ES — "no encuentro <TICKER>-USD en hyperliquid", "puedo operar <acción> en hyperliquid perp".
created: 2026-07-02
source: builtin
---

On `hyperliquid_perpetual`, **tokenized perpetual contracts** (equities / pre-IPO
names such as `SPCX-USD`) are **not listed directly**. They are issued by a
provider, and the issuer's prefix is part of the trading pair symbol.

## The rule

Prepend the issuer prefix to the pair:

```
XYZ:SPCX-USD      ✅ correct
SPCX-USD          ❌ not found
```

The biggest issuer is **XYZ**, so `XYZ:` is the default prefix to try.

## When to apply

When a user asks to trade a tokenized perp on `hyperliquid_perpetual` and the
plain pair (e.g. `SPCX-USD`) can't be found:

1. Check whether the underlying is a **tokenized asset** (equity / pre-IPO
   ticker, not a native crypto).
2. If so, retry with the issuer prefix — `XYZ:<TICKER>-USD` by default — **before**
   concluding the pair is unavailable.
3. Only report "not available on hyperliquid_perpetual" after the prefixed form
   also fails.

This applies anywhere a hyperliquid perp pair is resolved — quoting, placing an
order, or deploying an executor/controller.
