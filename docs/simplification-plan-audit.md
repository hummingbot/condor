# Simplification Plan Remediation Report

Date: 2026-07-15

Branch: `spike/simpler-agent-framework`

Baseline: original audit at commit `6a63f5c`; authentication removal at `d71b53a`

## Verdict

The audited implementation is substantially corrected. The application imports, the
backend suite passes, frontend lint and production build pass, executor creation is
bound to an exact account, and the persistence/risk regressions reproduced by the
original audit now have automated coverage.

One financial-lifecycle gap remains: an interrupted run can now stop or close durable
nonterminal executors and can re-close a detached position, but `control_run(close=true)`
does not synthesize a cleanup trade for inventory left by an already-terminal
single-leg order executor. That inventory remains attributed, contributes to risk, and
blocks agent deletion, so the system fails closed; an operator must currently dispose
of it with an explicit opposing executor. This should be addressed before declaring the
entire simplification plan complete.

The large agents web adapter is also still a maintainability recommendation, not a
financial correctness blocker.

## Remediation status

| Audit item | Status | Resolution |
|---|---|---|
| RB-1 — partial auth deletion broke imports/build | Fixed | Removed stale Python/TypeScript/CLI auth references and dependencies; loopback Host/Origin/CSRF posture remains the sole dashboard gate. Backend collection, app imports, frontend lint, and frontend build pass. |
| RB-2 — placeholder account binding and connector cache | Fixed | Agent specs resolve display selectors to canonical custody addresses. Frozen specs, run capabilities, executor configs, replay identity, leases, snapshots, recovery, and account guards carry exact `AccountRef`. Production connectors are constructed per executor from exact-account credentials and are closed with that executor. |
| RB-3 — terminal inventory absent from risk | Fixed | Open-executor count still uses nonterminal records, while exposure folds all attributed records by exact account/instrument. Confirmed signed base inventory, landed exits, and worst-case live remainders are included without cross-account netting. |
| RB-4 — stop/restart/watchdog depended on memory | Partially fixed; residual noted above | Stop enumerates durable records and reconstructs missing nonterminal executors. Detached positions cancel protection orders before becoming terminal and may be reactivated for explicit closure. Watchdog uses the signed `orders[]` close path and persists cleanup transitions. Already-terminal single-leg inventory still needs the cleanup service described below. |
| H-1 — torn notification tail and false success | Fixed | The writer truncates a malformed final record under its lock, fsyncs appends, fsyncs the parent when creating the outbox, and propagates persistence failure. |
| H-2 — AccountStore lost concurrent updates | Fixed | All instances for one path share a process-wide reentrant transaction lock. Read/modify/validate/write is atomic, temp names are unique, and file plus parent directory are synced. Executor registration revalidates the credential snapshot under the same guard. |
| H-3 — replay crossed owner/run/account boundaries | Fixed | Replay now compares request hash, origin, slug, run/connection owner, and exact `AccountRef`; mismatches return conflict. |
| H-4 — approval persistence failed open | Fixed | Decisions are appended before in-memory grants or waiter release. Disk failure leaves the request unapproved. An approval whose outbox notification cannot be persisted is durably denied when possible and never waits invisibly. |
| H-5 — deletion guard failed open | Fixed | Deletion reads durable records even without a runtime, lets projection errors fail closed, and blocks nonterminal records, live landed orders, detached holdings, and terminal nonzero attributed inventory. |
| H-6 — arbitrary launch overrides | Fixed | Launch accepts only trading context, `max_ticks`, experiment/dry-run selection, and stricter risk limits. Model, account, frequency, stop behavior, and unknown strategy fields are rejected. |
| H-7 — dead Phase 6 surfaces remained shipped | Fixed | Removed the legacy setup script, legacy history migration parser, obsolete log-analyzer skill, dead Hummingbot MCP registration, and runtime `pydantic-ai` dependency. Setup now uses the Condor installer and the OpenClaw script registers only Condor. Shipped skills/integration docs and frontend agent types were updated. |
| M-1 — narrow grep gate and prompt-only dry run | Fixed for the reported gap | The gate scans code, frontend, scripts, manifests, skills, and integrations. It runs a real one-tick experiment through lifecycle, freeze, providers, risk gate, and RunStore with a venue connector trap proving zero venue calls. Existing network/control tests cover app and socket surfaces. |
| M-2 — frontend lint failures | Fixed | Removed compiler-hostile memoization, corrected WebSocket callback/reconnect ownership, and documented the intentional local-draft synchronization exceptions. ESLint is clean. |
| M-3 — lint tools not installed by documented sync | Fixed | Black, isort, pytest, and pytest-asyncio are in the `dev` dependency group used by `uv sync --dev`; `make lint` is check-only. Lockfile updated. |
| M-4 — agents web adapter remains large | Recommended follow-up | Mutation authority is service-owned, but read/projection routes remain consolidated in a large adapter. Split read services/routers in a separate low-risk refactor. |

## Important implementation details

### Exact account authority

The execution identity now follows one immutable chain:

`AGENT.md selector → canonical AccountRef → frozen spec → capability → executor opener → connector/lease/snapshot/recovery`

Caller wallet and network strings are overwritten from the server-resolved account and
registered venue. Agent executor calls cannot rebind the account. Direct calls may pass
a selector, which is resolved immediately. Two accounts at one venue receive independent
lease keys and persisted bindings.

Account edit/removal is rejected during startup reconciliation, for exact-account
nonterminal records, or while an exact-account lease is held. A create that loses a race
with an edit detects the changed credential snapshot and aborts before persisting the
opener.

### Risk and attributed inventory

Risk uses two distinct projections:

- `max_open_executors`: nonterminal records only.
- `max_position_size_quote`: all attributed records, grouped by account, instrument,
  and quote denomination.

Confirmed fills fold signed base quantity. Exits at a different price flatten base
inventory correctly rather than leaving profit/loss mistaken for exposure. Live buy and
sell remainders are evaluated as alternative worst cases, so opposing resting orders do
not prematurely cancel one another. A reducing exemption may use inventory only from
the requested exact account and instrument.

### Durable stop and detach

Stop and shutdown enumerate the append-only executor projection rather than only live
tasks. A durable nonterminal executor can be reconstructed with its exact account
connector and restarted into its stop state. Position-preserving stop transitions through
`DETACHING`, cancels and settles protection orders, and only then records a terminal
detached holding. Explicit close can reactivate that detached position and uses signed
landed-order inventory; the legacy scalar fallback is rejected.

The remaining terminal-order limitation is deliberately fail-closed: its holdings stay
in snapshots and cap calculations, and deletion is rejected. The recommended fix is one
durable scope-cleanup service that creates/persists opposing cleanup orders, associates
them with the original scope, handles cancel/fill races, and is shared by UI, MCP,
restart recovery, watchdog, run stop, and agent shutdown.

## Verification

The final verification commands are:

```text
.venv/bin/python -m pytest -q
npm run lint                 # from frontend/
npm run build                # from frontend/
python -m compileall -q condor mcp_servers tests
git diff --check
```

| Check | Final result |
|---|---|
| Backend suite | **549 passed** |
| Frontend ESLint | **Passed, zero warnings/errors** |
| Frontend TypeScript + Vite production build | **Passed** (non-blocking chunk-size warning only) |
| Python bytecode compile | **Passed** |
| FastAPI application construction | **Passed** |
| Shipped-surface legacy gate | **Passed** as part of the backend suite |
| Patch whitespace validation | **Passed** |

Focused regression coverage includes:

- multi-instance AccountStore updates and credential create/edit races;
- two same-venue accounts, exact persisted bindings, independent leases, and
  cross-owner replay rejection;
- terminal fills, partial/full exits, profitable flat exits, live reservations, and
  cross-account risk isolation;
- durable runtime stop, detached protection cancellation, signed close sizing, and
  watchdog persistence;
- deletion with no runtime and terminal attributed inventory;
- approval disk/outbox failures and notification torn-tail recovery;
- forbidden launch overrides;
- an actual experiment tick with zero venue connector calls;
- repository-wide shipped-surface legacy grep.

## Remaining recommended work

1. Implement automatic durable cleanup for already-terminal single-leg inventory, then
   add interruption tests at each cleanup write/cancel/fill boundary.
2. Add a fake-venue restart acceptance that submits through two credential-distinct
   accounts at the same venue, restarts the runtime, and verifies all poll/cancel calls
   retain their original account.
3. Split the large agents dashboard adapter into smaller read services/routers.
4. Consider frontend code-splitting; the production build is successful but reports a
   non-blocking main-chunk size warning.
