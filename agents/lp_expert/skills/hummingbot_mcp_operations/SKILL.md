---
name: hummingbot_mcp_operations
description: Use Hummingbot MCP tools safely by choosing the narrowest native capability, preserving exact scope, and reconciling uncertain results.
when_to_use: Read before using an unfamiliar Hummingbot MCP action, when a tool response is ambiguous, or when deciding whether a native tool already covers a required operation.
source: agent:lp_expert
---

# Hummingbot MCP Operations

1. Treat the current tool descriptor and this Agent's action policy as the
   callable authority. Documentation describes capabilities; it does not prove
   that a tool or action is exposed in this session.
2. Use the highest-level native tool that directly owns the job. Do not replace
   an available portfolio, pool, executor, or status action with a custom
   workflow.
3. Preserve the current controller, account, network, wallet, executor, pool,
   and operation identities across related calls. Never infer one identity from
   another.
4. Before mutation, inspect the live schema and current state, apply the active
   Strategy's limits, record intent when the mode provides a journal, and submit
   once.
5. Classify timeouts, cancellations, transport failures, missing identities, and
   contradictory responses as uncertain. Reconcile authoritative state; never
   blind-retry.
6. Keep credentials, RPC URLs, API keys, and private configuration out of tool
   arguments, journals, and responses. Never change Hummingbot or Gateway
   configuration during a trading tick.

Read `tool-map.md` only when choosing an exact tool/action or diagnosing a
missing or unclear capability.
