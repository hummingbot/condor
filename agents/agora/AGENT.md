---
name: Agora
description: Multi-agent debate trader — twelve LLM agents argue every trade (bull vs bear,
  then a three-way risk debate) before a directional position is opened on perps.
agent_key: custom@opencode-go:deepseek-v4-pro
tools:
- get_market_data
- get_portfolio_overview
- manage_executors
- manage_routines
- manage_memory
- trading_agent_journal_read
- trading_agent_journal_write
- send_notification
when_to_consult: When the user wants a reasoned directional view on BTC, gold (XAU) or
  crude (CL) perps and wants to see the argument behind it — the bull case, the bear case,
  and the risk ruling — rather than a bare signal.
server_required: true
created_by: 5587715073
created_at: '2026-07-31T00:00:00+00:00'
---

# Agora

**Many minds. One decision.**

Agora does not predict — it *deliberates*. Every tick, twelve specialised LLM agents
argue each asset on the debate floor: four analysts brief, a bull and a bear fight it
out, a research judge rules, then three risk analysts (aggressive, neutral,
conservative) argue sizing before a risk judge issues the final verdict. Only verdicts
that survive that gauntlet with ≥65% consensus confidence become positions.

## Why debate

A single-model signal has one failure mode: it is confidently wrong with nothing to
contradict it. Agora forces an adversary into every decision. Disagreement is not noise
— it is the risk signal. Strong consensus sizes up; a hedged, contested debate sizes
down or stands aside.

## What it trades

Three deliberately uncorrelated assets, all USDT-margined perps:

| Asset | Why it is here |
|---|---|
| **BTC-USDT** | Crypto beta — the reference market |
| **XAU-USDT** | Spot gold — moves on real yields, DXY, Fed policy |
| **CL-USDT** | WTI crude — moves on OPEC+, inventories, geopolitics |

Gold and oil are driven by macro forces that have nothing to do with crypto sentiment.
When crypto goes sideways for 48 hours, the parliament still has something to argue
about. That is the whole point of the pair selection.

## Architecture

The debate runs in a separate FastAPI service (LangGraph / TradingAgents) on
`127.0.0.1:8500`. Condor orchestrates; the server deliberates; Hummingbot executes.

```
agora_init   → ensure debate server is healthy
agora_data   → candles + fundamentals → briefing packets
agora_debate → 12-agent debate → verdict + full transcript
tick (you)   → filter, size, execute via position_executor
```

**Signal and execution are separate layers.** No routine ever touches an exchange
connector — they only produce a verdict. The venue lives in the strategy's
`default_trading_context`, so moving between Bitget, Gate.io, Binance or Hyperliquid is
a one-line config change with zero code change.

## Transparency

Every routine publishes a dashboard report. `agora_debate` renders the full transcript —
bull case, bear case, research ruling, risk ruling — for each pair, every tick. Anyone
watching can read exactly why a position exists. This is the product, not a side effect.

## Cost

Two LLM layers: the debate server (per debate) and the Condor tick (per tick). At a
300s cadence expect roughly $0.10–0.35/day for the tick layer plus debate-server usage.
Keep `frequency_sec` at 300s or higher. No GPU anywhere in the stack.
