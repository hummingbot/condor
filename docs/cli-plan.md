# Condor CLI Plan

A proper operator CLI modeled on **`hbot`** (`~/hummingbot/hummingbot/cli` —
the Hummingbot Foundation's non-interactive CLI; Condor is a Foundation
product, so the closer the family resemblance the better), scaled to
Condor's posture: one operator, one process, loopback-only, everything on
disk append-only. The CLI is the harness-independent control surface;
today's stop-hang incidents (agent-history-comparison §9.3/§9.4) proved why
it must exist: when the chat harness wedges, a terminal must be enough.

## Lessons taken from `hbot`

`bin/hbot` + `hummingbot/cli/README.md` encode conventions Condor adopts
wholesale:

1. **Dual-audience output.** Every command emits compact **Markdown**
   (tables for lists, `- key: value` for records) — "readable by humans and
   agents alike" — and run/observe commands take **`--json`** for raw
   values. Not cosmetic: the Foundation's own eval
   (`~/hummingbot/hbot-cli-vs-mcp-eval.md`) had an agent launch a real bot
   through the hbot CLI with **22% fewer tokens and 21% less wall-clock**
   than through the MCP server. The CLI is an *agent surface* too, not just
   a human escape hatch.
2. **The machine contract is the exit code, not the text.** Adopt hbot's
   table verbatim: `0 SUCCESS · 1 ERROR · 2 NOT_FOUND · 3 NOT_RUNNING ·
   4 CONFIG_ERROR · 5 TIMEOUT`. Errors to stderr as
   `Error: <message> (code N)`.
3. **Flat, alphabetical surface.** No nested sub-commands (openclaw's 60
   nested commands is the anti-model here); detail lives in
   `condor <command> -h`, not the menu. The help menu groups commands into
   the same ontology hbot uses: *set up · run & control · observe &
   maintain*.
4. **`doctor`'s charter, verbatim**: "runs the checks whose failures
   otherwise surface one at a time as confusing runtime errors." Any `fail`
   row exits 1; warns are advisories and exit 0. hbot's check list adds one
   Condor missed: **clock skew vs internet time** — signed venue requests
   (Hyperliquid, Solana) reject drifted clocks.
5. **One-shot verbs for agents.** hbot's `deploy` collapses
   config→running into one command because "agents and scripts don't need
   the intermediate steps". Condor's `start <slug> --dry-run/--context` is
   the same idea (and named `start` to match hbot's start/stop pair).
6. **Never secrets on argv.** hbot takes `HBOT_PASSWORD` / `--password-stdin`
   only. Condor's CLI currently needs no secrets (sealed store + loopback);
   keep it that way — any future secret input follows the same stdin/env
   rule.
7. **A short real binary.** `bin/hbot` is symlinked onto PATH by the
   installer; Condor gets `condor` via `[project.scripts]`.
8. **Roadmap discipline.** hbot v1 is "a faithful subset" with a
   deferred-commands table where every row names its v1 alternative. This
   plan's tiers follow that pattern: defer loudly, with the workaround
   stated.

## A second comparison: the Hermes CLI/TUI

Hermes (hermes-agent.nousresearch.com) is an *interactive agent TUI* rather
than an ops CLI over a trading daemon, so most of its surface (personas,
voice, slash commands, context compression) doesn't transfer. Three things
do:

1. **No daemon, nothing to wedge.** Hermes is deliberately
   stateless-per-session with SQLite persistence — no gateway process.
   That's the same conclusion our stop-hang incidents forced about
   openclaw's client/daemon split, from the opposite direction: Condor's
   one foreground `serve` + a stateless CLI is the family-correct shape.
2. **Status is free.** Hermes' `/status` is "pure local compute; no LLM
   call" — instant and deterministic. `condor status`/`doctor` hold the
   same bar: socket round-trips and file reads only, never a model call.
3. **Interruption is the UX priority.** Hermes treats interrupt-and-redirect
   as the core interaction (type to interrupt, double Ctrl-C to exit). The
   ops-CLI translation is what `condor stop` already is: the interrupt path
   must be instant and must not share fate with whatever is being
   interrupted. (Notably, Hermes ships no `doctor` — hbot is the better
   model there.)

## Principles

1. **Thin views, no state.** Every command is a read of the append-only
   files or one call over the control socket (`store/condor-control.sock`).
   The CLI owns no configuration, no cache, no daemon of its own.
2. **Degrade gracefully.** File-backed commands (`runs`, `learnings`,
   `agents`) work with the server down; socket-backed ones exit
   `3 NOT_RUNNING` with "control socket unavailable — is `condor serve`
   running?".
3. **Loopback posture (§5.5).** No auth flags, no remote mode. The socket
   IS the permission.
4. **argparse, one file, stdlib only** until it demonstrably hurts. No
   click/typer dependency for a five-verb CLI.
5. **Escape-hatch parity**: anything a wedged harness might leave dangling
   (live runs, pending approvals) must be resolvable from the terminal.
6. **hbot conventions** (§ above): markdown + `--json`, stable exit codes,
   flat surface, grouped help.
7. **Standard verb grammar.** Collection commands are **plural nouns**
   (`accounts`, `agents`, `runs`, `reports`, `routines`, `learnings`) and
   the bare verb **lists the collection, most recently modified first** —
   `condor accounts` lists accounts, `condor reports` lists reports,
   `condor runs` lists runs. Detail/actions hang off the same verb
   (`condor accounts add <venue>`, `condor reports show <id>`,
   `condor agents <slug>`). Lifecycle verbs match hbot: **`start`** / `stop`
   (not `run` — hbot's vocabulary wins for family resemblance).

## Today (2026-07-16)

| Command | Does |
|---|---|
| `init` | harness onboarding (idempotent, multi-select) |
| `update` | git pull current branch + `uv sync` (`--check-only`) |
| `serve` | THE process: control socket + scheduler + runtime + web |
| `stop [run\|slug] [--close]` | stop live runs over the socket (shipped during incident 3) |
| `accounts [import-env]` | bare verb lists accounts (last-modified first, redacted); `import-env` = sealed store onboarding |

Also planned as step 0: a `[project.scripts] condor = "condor.cli:main"`
entry point so `condor …` works without `uv run python -m condor.cli …`.

## Surfaces: is the dashboard just another client — and do we need three?

An honest inventory. Condor has ONE authority (AgentService + ExecutionService
behind the control socket) and today **three user-facing clients** over it:

| Surface | Transport | Gate | Mutations exposed | Maintenance cost |
|---|---|---|---|---|
| MCP (harness chat) | stdio subprocess | process spawn — no listener | full (typed tools, risk/scope-gated per call) | the tool layer — shared with the product itself |
| Web dashboard | loopback HTTP, **no auth** | bind address only | **22+ routes**: agent CRUD, start/stop/shutdown, directives, learnings replace, account/venue management | FastAPI routes (thin) + a 40-file React frontend + npm toolchain (the heaviest artifact in the repo per unit of use) |
| CLI | unix control socket | filesystem permissions | `stop` today; Tier 1–2 planned | one stdlib file |

(The control socket itself is transport, not a fourth surface — MCP and the
CLI both ride it.)

### Which is more secure? Not close.

The **socket/CLI and MCP are equivalently strong**: no network listener at
all; you must already be able to run code as the user. The **dashboard is
the weakest surface by a class**, not a degree: an unauthenticated loopback
HTTP server is reachable by *any local process* and — the part loopback
doesn't protect against — by *web pages in your browser*. Cross-origin
form-POSTs fire at `127.0.0.1:3000` without needing to read the response,
and DNS-rebinding turns "loopback-only" into "whatever your browser can be
tricked into". §5.5 deleted auth on the reasoning that loopback is the
gate; that reasoning holds for the socket, but for HTTP it leaves every
mutating route — including *delete agent* and *account management* — one
malicious tab away. The CLI is not just operationally necessary
(three stop-hang incidents); it is the **most secure mutation path we
have**, and the only one that shares no fate with a wedged harness.

### The Hermes lesson on consistency

Hermes maintains consistency between web and terminal by **refusing to have
two operational surfaces**: "a full terminal user interface — not a web UI."
Its web component (Nous Portal) exists only for setup/auth — the one flow
where a browser is genuinely better than a terminal. Everything operational
lives in the terminal family. hbot goes further: no web surface at all.
The family pattern is clear: *one operational surface per audience, web
only where the browser earns it.*

### Do we really need the CLI? And should the dashboard go?

Yes to the CLI — three independent grounds: the escape hatch is proven
(incidents 1–3), it is the strongest-gated mutation path (above), and the
Foundation's own eval shows it is the *cheapest agent surface* (−22%
tokens vs MCP for a launch task). It also **enables** the dashboard
decision rather than adding a third mutation surface long-term:

**Recommendation — converge on two mutation surfaces, demote the dashboard
to setup + read-only monitoring** (which is already how the README frames
it: "used for setup and monitoring"):

1. MCP = where you *think* (create, converse, manage agents from a harness).
2. CLI = where you *operate* (status, doctor, stop, approvals, accounts,
   scripts — and the escape hatch).
3. Dashboard = where you *watch*: **fully read-only** — strip every
   mutating route, keep the read views (run history, executor performance,
   routine reports, later a TradingView chart). This deletes the CSRF-class
   exposure entirely and most of the frontend's future maintenance
   (mutation panels), while keeping its genuine UX edge: glanceable visual
   monitoring, which neither markdown tables nor chat can match. (Earlier
   drafts carved out a "setup flow" exception à la Hermes' portal; moving
   account add/remove to the CLI — below — removes the need: credentials
   entry over unauthenticated loopback HTTP was the single worst route to
   keep.)
4. Revisit full dashboard deprecation only after Tier 1–2 land and
   `status --watch` / `runs export` exist — if the read views stop earning
   their React upkeep, the remaining step is small. Deprecating today would
   trade away real monitoring UX before its replacement exists.

Owner decision: step 3 approved 2026-07-16 (with the migration map below);
the demotion lands only after each function's CLI home ships.

### Where each dashboard function goes (the migration map)

hbot's rule: defer loudly, with the workaround stated. Every function the
dashboard performs today gets a named destination BEFORE its routes are
stripped — nothing is orphaned:

| Dashboard function today | CLI home (mutations + scriptable access) | Read-only dashboard keeps | Notes |
|---|---|---|---|
| **Add/remove venue accounts** (POST/PUT/DELETE `/venues/*/accounts`, set default) | `condor accounts` lists (last-modified first, redacted); `accounts add <venue>` (`--fields` lists what the venue needs, à la `hbot connect --fields`; key material via env or `--keys-stdin` — **never argv**, hbot rule 6), `accounts remove <address>`, `accounts default <address>` | redacted account listing (view) | The highest-stakes migration: credential entry moves OFF the no-auth HTTP surface entirely. Sealed store + read-only probe semantics unchanged (`import-env` stays for legacy env onboarding). |
| **View routine reports** | `condor reports` lists (last-modified first); `reports show <id>` prints one as markdown (they are markdown already; dual-audience output for free); `condor routines` lists routines (last-modified first) + `routines run <name>` / `routines schedules` for the run/schedule mutations the dashboard exposes today | report viewer (browser renders tables/charts better than a terminal) | CLI = access + scripting; dashboard = pretty rendering. Report DELETE moves to `condor reports rm <id>`. |
| **Executor performance** | `condor performance [slug] [--group-by agent\|run\|venue\|type]` — the `executor.performance` control method already computes this; the CLI is its missing consumer. Aggregates also surface in `condor status` | performance tables now; the planned **TradingView chart** later — this is the flagship "browser earns it" view and the main reason the read-only dashboard exists | Numbers in the terminal, curves in the browser. |
| **⌘K agent pane** (chat over `chat_ws`) | none — **retires** | none | Chat's home is the harness (MCP): that IS the product's primary surface, strictly more capable than an embedded pane. Retiring it also deletes the `chat_ws` session-identity machinery — real complexity for a secondary feature. |
| Agent CRUD / start / stop / directives / learnings edit | already MCP; CLI: `stop` (shipped), `start`, `approvals`, `learnings --edit` (Tier 1–2) | run history, live run views | These routes strip once Tier 2 lands. |

Sequencing constraint: **routes strip per row, not big-bang** — each
dashboard mutation disappears in the same change that ships its CLI
replacement, so there is never a gap where a function has no home.

## Tier 1 — operate (next)

The commands today's incidents would have wanted, in priority order:

- **`condor status`** — one glance: server up (socket ping + version/uptime),
  live runs (slug, seq, tick, PnL, started), scheduler next-fires, open
  executor count/exposure per agent, pending approvals count. Sources:
  `agent.list`, `executor.list`, the runs dir. (openclaw analog: `status`,
  `health`.)
- **`condor doctor [--fix]`** — hbot's charter: the checks whose failures
  otherwise surface one at a time as confusing runtime errors. One
  PASS/WARN/FAIL row each + a remedy; any fail exits 1, warns exit 0:
  - control socket exists and answers (`agent.list` round-trip timed);
  - `.direct-token` fresh vs server start;
  - **stale clients**: `mcp_servers.condor` processes older than the server
    process (today's openclaw-gateway wedge — 7:56 children surviving two
    server restarts) → "restart your harness/gateway";
  - **clock skew vs internet time** (from hbot: signed venue requests
    reject drifted clocks — Hyperliquid signatures, Solana blockhashes);
  - interrupted run streams awaiting sweep (`run_started` w/o `run_ended`);
  - venue read probes per configured account;
  - runs/artifacts disk usage vs retention policy (§9.4.4, still open);
  - learnings at/near the 40 cap per agent ("consolidation pressure");
  - frontend assets built (serve non-headless);
  - config/spec validation across `agents/*/AGENT.md` (unknown
    `entry_guards` names would be caught HERE instead of at trade time).
- **`condor runs [slug] [--kind k] [-n 20]`** — bare verb lists run metas,
  last-modified first — i.e. by latest event, so a live run being ticked
  sorts above an older `run_ended` one —
  (id, seq, kind, status, ticks, started/ended). `condor runs export
  <run_id>` → `render_run_markdown` to stdout (the exporter finally gets a
  consumer). File-backed: works with the server down.
- **`condor logs [-f]`** — requires the one real gap first: `serve`
  currently logs to stdout only, which is why incident 2's model-switch
  cause couldn't be settled. Add rotating file logging under
  `store/logs/condor.log`, then `logs` tails it (openclaw analog: `logs`).

## Tier 2 — manage (includes the dashboard-migration commands)

The migration-map commands (marked ▣) gate the dashboard demotion — each
strips its dashboard route when it ships:

- ▣ **`condor accounts add <venue> / remove <address> / default <address>`**
  — full account lifecycle from the terminal (`--fields` for discovery,
  keys via env/stdin only); the bare verb already lists. Highest priority
  of the tier: it moves credential entry off the no-auth HTTP surface.
- ▣ **`condor performance [slug] [--group-by agent|run|venue|type]`** —
  CLI consumer for the existing `executor.performance` method.
- ▣ **`condor reports [show <id> | rm <id>]`** and
  **`condor routines [run <name> | schedules]`** — bare verbs list
  (last-modified first); routine reports are markdown already, so
  printing them is the dual-audience contract for free.
- **`condor agents [slug]`** — bare verb lists agents last-modified first
  (slug, model, denomination, schedule, live runs); with a slug, shows one
  spec summary + hashes.
- **`condor approvals [resolve <id> approve|deny]`** — the second
  escape-hatch: pending permission events survive a wedged harness too;
  default-deny timeout shouldn't be the only fallback.
- **`condor learnings <slug>`** — print the file; `--edit` opens `$EDITOR`
  (operator curation without the web route).
- **`condor memory [search <q>]`** — the global store's index/search
  (read-only view; writes stay in chat).
- **`condor start <slug> [--dry-run] [--max-ticks N] [--context "…"]`** —
  start a session/experiment from the terminal (parity with `run_agent`;
  completes the hbot-style start/stop pair).

## Tier 3 — platform (when needed, not before)

- `condor version` (git rev + spec/schema versions), shell `completion`,
  `dashboard` (open the local web UI), `backup` (tar `store/` + `agents/`
  — everything is files by design, so backup is `tar`).
- `serve --daemon` + `condor serve stop|restart` (launchd/systemd wrapper)
  only if foreground-in-a-terminal stops being enough.

## Deliberately not adopted from openclaw

Channels/pairing/devices/directory (multi-channel chat is the harness's
job), plugins/sandboxes (no third-party code execution surface), profiles
(single operator), gateway management (Condor's `serve` is one foreground
process — the complexity openclaw's `daemon`/`gateway` commands manage is
the same client/daemon split that caused today's stop-hangs).

## Sequencing

1. **Contract first** (from hbot): the exit-code table, markdown/`--json`
   output helpers, and the `[project.scripts]` entry point — so every
   later command is born conformant. Retrofit `stop`/`update`/`accounts`
   onto the contract.
2. `status` (pure reads, ~an hour).
3. `doctor` with the checks above (each is a few lines against existing
   APIs; the stale-client check is `ps` + process start-time comparison).
4. File logging in `serve`, then `logs`.
5. `runs` / `runs export`.
6. Tier 2: `accounts add/remove` first (credential entry leaves HTTP; strips
   the venue mutation routes), then `approvals` (safety parity), then
   `performance`/`reports`/`routines` (each stripping its dashboard route
   on landing), then `start`/`agents`/`learnings`/`memory`.
7. When the last ▣ row lands, the dashboard is fully read-only and the
   `chat_ws` pane + session machinery are deleted with it.
