# Market Reporter manual data verification

Run the same public gather path used by the agent directly from the Condor
repository root:

```bash
PYTHONPATH=. .venv/bin/python -m agents.market_reporter.routines.gather_data crypto
PYTHONPATH=. .venv/bin/python -m agents.market_reporter.routines.gather_data tradfi --history-days 180 --budget 60
PYTHONPATH=. .venv/bin/python -m agents.market_reporter.routines.gather_data memecoin --budget 60
```

Add `--focus SYMBOL` more than once for explicit focus assets, `--theme TEXT`
for current-session themes, or `--full` to print the complete compact analyst
payload. Raw source bundles are never printed; they remain in the process-local
run-bound evidence snapshot used by `build_market_report`. Without `--full`,
the command prints coverage plus `debug_trace`.

Memecoin diagnostics now expose two deliberately separate layers:

- CoinGecko provider categories and their bounded top constituents supply the
  broad theme and chain sample used by the report.
- GeckoTerminal and DEX Screener supply the smaller exact-pair screen used for
  liquidity, pair age, turnover, and eligibility checks.

The CoinGecko path works against its documented keyless public API. For a less
volatile free rate limit, set `COINGECKO_DEMO_API_KEY`; the value is sent only
in the request header and is never included in diagnostic output. Category
requests are spaced and HTTP 429 responses receive one bounded retry. Keyless
mode stays within five requests by expanding the largest theme, the fastest
absolute 24-hour mover, and a Robinhood-linked theme; the category-summary
layer still covers all six.
The free Demo key expands constituent coverage across all six themes and adds
CoinGecko's Solana/Robinhood category memberships. Missing CoinGecko enrichment
leaves the existing CoinMarketCap and exact-pair evidence available, so it
cannot make the whole market bundle unavailable.

The command is read-only. It uses the fixed provider manifest, does not create a
Condor session or report, does not write a journal, and emits JSON only to
stdout. Exit code `0` means every source bundle completed; exit code `1` means
at least one source was partial or unavailable. A nonzero exit therefore
preserves useful diagnostics and does not imply that every source failed.

The trace records collector start/end times, duration, terminal outcome,
retained item count, provider count, bundle error/warning counts, total cached
source bytes, analysis-context bytes, and emitted-payload bytes. It never
includes response bodies, credentials, or query parameters. A snapshot expires
after 30 minutes and is consumed when a report saves successfully. Because the
manual CLI exits after gathering, its printed snapshot ID is diagnostic only;
the normal Agent gather and build calls share one server process where the
snapshot remains resolvable.

## Report-builder regression checks

The builder accepts a typed `AnalyticalDigest`; evidence-owned fields are not
part of the LLM tool input. During the one build call it:

- restores the exact run-bound source bundles and audit manifest;
- injects retained official scheduled events and merges analytical watch
  conditions;
- attaches the retained Crypto or TradFi aggregate breadth evidence ID to
  claims that require it; and
- computes a downward-only coverage/confidence ceiling from bundle status,
  strategy gates, source limitations, and verified-event coverage.

Run the isolated Agent suite before a live run-once verification:

```bash
PYTHONPATH=. .venv/bin/pytest -q agents/market_reporter/tests/test_market_reporter.py
```

The regression suite removes the Crypto breadth ID, removes all LLM-authored
TradFi events, and overclaims TradFi coverage. Each case must save in one build
call after deterministic hydration, while numerical contradictions and unknown
evidence IDs remain hard failures.
