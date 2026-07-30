# Hummingbot MCP Tool Map

Use this map as navigation. The live descriptor and `AGENT.md` action policy
remain authoritative.

## LP Expert surfaces

| Job | Preferred surface | LP Expert use |
|---|---|---|
| Discover and supervise executors | `manage_executors` | Schema, search, position summary, performance, logs, exact stop |
| Read balances and LP holdings | `get_portfolio_overview` | Current account/network evidence |
| Read supported DEX pools | `explore_dex_pools` | Active-venue follow-up evidence |
| Read secondary pool/OHLCV data | `explore_geckoterminal` | Corroboration, never sole mutation authority |
| Run Agent-local deterministic capabilities | `manage_routines` | Only routines allowed by `AGENT.md` |
| Load static playbooks and companions | `manage_skill` | `read` and `read_file` only |
| Record loop-mode intent | `trading_agent_journal_write` | One current-session action entry |

`manage_executors` is the portfolio lifecycle surface. Gateway is the chain and
DEX transaction layer. Do not use Gateway calls to invent executor ownership or
use executor calls to infer a wallet.

## Progressive discovery

1. Read the tool descriptor or schema before supplying unfamiliar fields.
2. Search/list before requesting broad detail.
3. Narrow by the exact current controller and then by executor or pool identity.
4. Treat pagination, truncation, stale timestamps, and partial result sets as
   incomplete evidence.
5. Use returned identifiers verbatim in reconciliation calls.

## Execution modes

- Dry run: read-only. Do not journal or call mutating actions.
- Run once: one live tick, no journal, and no follow-up tick. Start only a
  transition that can be confirmed or safely escalated in the same tick.
- Loop: one bounded action per tick with a current-session intent journal.

Infer these modes from the injected prompt markers, never from an `_eN` suffix.

## Availability and errors

- An action mentioned in documentation may be absent from the installed MCP
  version. Do not fabricate it.
- A validation rejection before submission is a definite failure. A timeout or
  lost response after possible submission is uncertainty.
- Error prose is not proof that no external mutation occurred. Reconcile through
  executor search or transaction status using the same identity tuple.
- LP Expert currently reaches Gateway swap operations through its guarded
  `gateway_swap` routine because direct Gateway tools are outside its allowlist.
