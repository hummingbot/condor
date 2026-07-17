# Condor Simplification Plan (v5)

**Status:** Phases 1–6 IMPLEMENTED (2026-07-15, this branch). Both
previously operator-gated exceptions are now closed:

1. **Auth deletion (§5.5 final step) landed 2026-07-15** (operator
   go-ahead given) — `condor/web/auth.py`, `routes/auth.py`, the JWT
   chat-WS handshake, `config_manager.py` (user roles), `python-jose`, and
   the CLI login-token/ADMIN_USER_ID remnants are deleted. The chat WS now
   identifies a browser by an opaque `client_id` (uuid4 hex, `?client=`
   query param, no authority) purely for session-slot continuity; loopback
   bind + Host/WS-Origin validation + X-Condor-Token CSRF posture (§5.5) is
   the sole trust boundary. The Phase 6 grep gate's allowlist is now empty.
2. **§7.3 legacy history deletion landed 2026-07-15** (operator
   go-ahead given) — `agents/*/sessions/` and the tracked
   `routine_builder/delegations/` are deleted; nothing read them.

Everything else is live: RunStore + approvals + scheduler + notifications
durability + routine worker (Phase 4), explicit MCP tools (Phase 5),
Telegram/Hummingbot/pydantic-ai deletion, store re-keying, serverless-only
ACP agents, `condor serve`, and the frontend sweep (Phase 6). Account
cutover (§12) was executed 2026-07-15.
**Date:** 2026-07-15
**Inputs:** two independent full-system surveys + ~27 review rounds
(scope, account model, ownership, lifecycle) — every finding resolved in
place; owner decisions recorded inline
**Owner-confirmed scope decisions:**
- **Telegram is removed entirely.** Users interact through MCP inside their
  harness (Hermes Agent, OpenClaw, Claude Code) or the web dashboard.
  Notifications and approvals surface in those channels.
- **Hummingbot is removed entirely** — not an optional package. CEX
  connectivity, if ever needed, enters later as an ExecutionService venue
  adapter.
- **No authentication or authorization.** Condor runs on the user's local
  machine, single user. The web dashboard is a local setup/monitoring tool —
  no login, no principals, no tenancy.
- **Agents keep routine authoring — and routines are READ-ONLY.** Routines
  are a product feature agents may create/edit (via `routine_builder`), but
  their role is strictly: (a) provide data that agents/executors consume,
  (b) generate reports for humans. Routines never execute trades — not even
  through `manage_executors`. All execution belongs to ExecutionService via
  executors.
- **No legacy history migration.** Old session/experiment/delegation
  directories are not exported or preserved by tooling; old-format parsers
  are deleted with them.
- One-off analysis routines: **DONE 2026-07-15** — all 35 root routines
  copied to `~/fengtality/condor-routines/` for safekeeping; the 27 one-offs
  deleted from the branch (6 generic primitives + `base.py` remain).

## 1. Product boundary

Condor's product: **let users create and run autonomous trading agents**,
operated from an agentic harness (via MCP) or a local web dashboard used for
setup and monitoring.

The repo currently contains two products: the original Telegram/Hummingbot
operations console (most of the LOC) and the agent platform. Only the second
survives. First visible act: rewrite `README.md` around the agent platform.

### Measured footprint (2026-07-15) and disposition

| Area | LOC | Disposition |
|---|---|---|
| `handlers/` (Telegram UI) | 59,188 | **deleted** — ~1.5k (agent context/gating/confirmation core) moves into `condor/` first |
| `frontend/` | ~34,000 | agent workflow (~5–6k) + minimal positions/settings stays; bots/backtesting/editor/portfolio pages and Login deleted |
| `condor/` | 28,230 | core — consolidated per §5–8; hb-api glue and web auth deleted |
| `routines/` (root) | was 13,524 | **DONE:** 6 primitives + infra remain; one-offs exported to `~/fengtality/condor-routines/` and deleted |
| `mcp_servers/hummingbot_api` | 7,917 | **deleted** |
| `mcp_servers/condor` | 2,772 | core — retooled per §8 |
| `utils/`, `main.py`, `config_manager.py` | ~6,000 | Telegram/hb parts deleted; server registry dissolves (§9.2) |

End-state target: **~35–45k LOC** (including frontend), one executor stack,
one mutation boundary, one history format, zero Telegram, zero Hummingbot,
zero auth machinery.

## 2. Target architecture

```
   Web dashboard (local HTTP/WS, no auth)     MCP harnesses (Claude Code, Hermes, OpenClaw)
                    \                                     /
                     +--------  AgentService  -----------+
                               /            \
                         Scheduler        RunStore   (append-only JSONL events,
                              |                       serialized writer, projections)
                         ModelRunner   (ACP only)
                              |
                         ToolGateway   (per-agent tool scoping, permission gates)
                              |
                       ExecutionService  (the only SUPPORTED trading-mutation
                              |            path: attributed-inventory risk, leases,
                              |            idempotent creates, reconciliation)
                              |
                 Solana  |  Hyperliquid  |  Polymarket
```

**Honest boundary statement:** because routines are agent-authorable trusted
code (owner decision, §7.2), arbitrary Python *can* import wallet loaders and
venue clients directly. ExecutionService is therefore the only **supported,
audited, risk-managed** mutation path — not a mechanically enforceable one.
Routines are read-only BY ROLE (data for executors + reports for humans,
§7.2); a mutation from a routine is a review defect that falls outside every
idempotency, risk, and reconciliation guarantee. The worker subprocess's
scrubbed environment and capability-free RunContext are defense-in-depth,
not a sandbox — a same-user process can still reach the filesystem and
socket if it tries.

Notifications/approvals (§4): emitters → outbox (source of truth) →
{MCP channel relay, dashboard WS, `get_notifications` tool}; approvals →
durable approval queue → {channel event + dashboard badge} → resolved by
`resolve_approval` MCP tool or a dashboard button → recorded as `permission`
events in RunStore.

### Do NOT simplify away

Deterministic executor/LLM separation (kind×instrument matrix, adapters);
venue-truth crash reconciliation; platform-enforced risk in the runtime create
path; dry-run (experiment) mode; human approval; immutable audit history;
native venue adapters (Jupiter, Hyperliquid, Polymarket CLOB); per-agent
isolation of tools/routines/skills/memory.

## 3. Storage decision: JSONL stays (hardened)

The executor log remains append-only JSONL (per-slug, transition-only,
fold-on-read) **as a format** — the store itself starts fresh under the new
schema (every opener carries an `AccountRef`, §6.2b; pre-v1 records are not
read). No SQLite anywhere; `executors/records.py` keeps its live
`ExecutorRecord` code — its stale "SQLite being retired" docstring and the
legacy run-id attribution helper (`slug_from_run_id`) are removed.

**Durability contract (the executor store is the SOLE financial recovery
authority — §6.2 — so it gets the same hardening as RunStore, which today's
writer lacks: no flush/fsync, and folding fails on a torn final line):**
one serialized main-process writer; **fsync on every recovery-relevant
transition** (opener, landed order, cumulative-fill digest change, status
change, close); `0700` directories / `0600` files;
torn-tail truncation on fold (a partial final line is ignored and truncated
before the next append). Acceptance: process death immediately after each
critical append category recovers cleanly.

The RunStore (§7) uses the same discipline with the hardening in §7.1
(serialized writer, sequence numbers, schema version, torn-tail recovery,
redaction, size caps).

## 4. Telegram removal: where notifications and approvals go

Telegram's two load-bearing jobs get first-class replacements **before** the
`handlers/` tree is deleted.

### 4.1 Notifications

- `store/notifications.jsonl` outbox stays the single source of truth
  (`condor/notifications.py` keeps its chokepoint role; the Telegram mirror
  branch is deleted) — **and gets the durability its "authoritative" title
  implies, which today's code lacks** (appends are not fsynced; the relay
  cursor is a raw byte offset, neither atomic nor partial-delivery-safe):
  - **one serialized main-process writer** (subprocess emitters enqueue
    via the control socket — same rule as `send_notification`, §8), fsync
    per append, torn-tail recovery on read;
  - every entry carries a **stable notification id** (monotonic seq or
    ULID); relay cursors track the **last delivered id**, not a byte
    offset, and are persisted **atomically (tmp+rename)** only after
    delivery — a crash between append, delivery, and cursor advance
    yields at-least-once delivery with id-based dedup, never silent loss;
  - acceptance: kill −9 between (a) append and delivery, and (b) delivery
    and cursor advance — both recover with no lost and no duplicate-
    displayed notifications.
- Delivery channels:
  1. **MCP channel relay** — `integrations/claude_channel` generalizes to a
     harness-agnostic relay (persistent offset + capped catch-up replay,
     built 2026-07-15). Claude Code today; Hermes/OpenClaw get the same
     tail-the-outbox contract.
  2. **Web dashboard** — WS push (small notifications topic) + the existing
     read route.
  3. **Pull** — the `get_notifications` MCP tool (exists).
- `notify()` loses `bot`/`chat_id` plumbing; `routine_store.py` stops holding
  a Telegram bot handle.

### 4.2 Approvals (human_gate)

- Durable **approval queue**: pending approvals are RunStore `permission`
  events (`status=pending`) plus an open-approvals index.
- Surfacing: a `kind=approval` outbox entry (reaches every §4.1 channel) and
  a dashboard pending-approvals badge/panel.
- Resolution: new MCP tool `resolve_approval(approval_id, approve|deny,
  note?)` — the harness agent relays the question to the human and calls the
  tool — or a dashboard button. The resolving **channel** is recorded.
- **Default deny on timeout** (configurable, e.g. 10 min), recorded as
  `permission {decision: timeout_deny}`.
- **Durable continuation (defined):** persisting the pending approval
  survives restart, but the suspended tool coroutine does not. Approval
  therefore does NOT resume a coroutine; it **grants a one-use token** keyed
  by `tool_call_id`/`executor_id`, consumed by the blocked call (if still
  live) or by a retry of the same create (same `executor_id`) within the
  SAME run. Resolution is
  idempotent (double resolve is a no-op; resolve-after-consume is a no-op).
- **Runs do not survive process death (defined):** engines are memory-only
  by design. On startup, any `run_started` stream lacking `run_ended` gets
  a synthesized `run_ended {status: interrupted}` (the RunStore successor
  of today's interrupted-session sweep); frozen spec, tick state, and
  directives are NOT restored — relaunching is a new run with a fresh
  capability. Consequently a pending approval whose run was interrupted is
  **voided at the same sweep** (`permission {decision: interrupted_void}`,
  surfaced as a notification) — the original run can never consume the
  grant, and a relaunched run must re-request approval under its own
  create. Restart with an active run + pending approval is an explicit
  adversarial test (§11).
- **Executors SURVIVE model-run death (deterministic, per kind):**
  executors are deterministic machines independent of the LLM loop, so
  run interruption never cancels or closes them. On startup the runtime
  re-adopts every nonterminal executor and continues its lifecycle —
  position-kind: barriers/protection keep managing the position;
  order-kind: the resting order keeps polling to terminal. The recovery
  OUTCOME is decided by venue truth, never by implementation choice
  (order already filled → fold the fill; still live → continue; venue
  says gone → resolve via terminal status/fill history per §6.2b).
  Orphan-by-interruption inventory stays agent-attributed and visible to
  the relaunched run as inherited state (§6.3). **`stop_run` (and
  `--close`) remains available for an interrupted/terminal run's
  surviving financial scope** — stop is a service/operator action keyed
  by run_id, not a capability-holder action, so the dead run's executors
  and orders can still be stopped at run scope without resorting to
  agent-wide shutdown.

### 4.3 Mechanical fallout

- Delete: all of `handlers/` (after §5.1 extraction), `utils/telegram_formatters.py`,
  `utils/telegram_helpers.py`, `utils/deeplink.py`, `utils/portfolio_graphs.py`,
  Telegram menu defaults in `preferences.py`, `main.py`'s
  `Application`/polling/`send_to_telegram`, admin `/update` flow (→ CLI),
  `condor_bot_data.pickle`.
- Routine signature: `run(config, context: ContextTypes.DEFAULT_TYPE)` drops
  the telegram-typed context for a plain optional `RunContext` (mechanical
  pass across all routine files).
- Dependency prune: `python-telegram-bot`.
- Identity: Telegram user ids disappear entirely — the `created_by` field
  is **removed** from AGENT.md/strategy frontmatter and all code paths (no
  auth and no legacy means no reason to carry inert numeric metadata). No
  replacement identity system (§5.5).
- **Store re-keying (the identity migration nobody owned):** memory and
  notification plumbing are keyed by Telegram-era `user_id`/`chat_id`
  today and must be re-keyed, or deleted identity leaks back through MCP
  settings and stores:
  - **Memory** becomes two tiers only: **agent-scoped**
    (`agents/{slug}/…`, unchanged) and **one local/global tier**
    (`store/memory/`) replacing the per-user dimension —
    `MemoryStore(user_id, slug)` loses its `user_id` parameter entirely.
    The existing `store/user_<id>/` directory is renamed once to the
    global tier (a one-time `mv`, not a compat layer).
  - **Notifications** are keyed only by **agent/run/channel metadata**
    (`agent_slug`, `run_id`, `kind`, delivery channel) — outbox entries
    drop their `user_id`/`chat_id` fields, and the MCP settings /
    `get_user_context` plumbing stops carrying a user identity.
  - Acceptance (Phase 6): no `user_id`/`chat_id` keys remain in store
    schemas, MCP settings, or notification entries.


## 5. Phase 2 — AgentService + agent core out of `handlers/`

### 5.1 Relocate the agent core (prerequisite for deleting `handlers/`)

Move into `condor/agents/`: `handlers/agents/_shared.py` (context builder →
`context.py`; tool classification → `gating.py`), `handlers/agents/confirmation.py`
(registry only — Telegram rendering dies), `handlers/agents/session.py`
(chat sessions, used by dashboard `chat_ws`). With Hummingbot removed there
is nothing to extract from `handlers/bots/` (the client itself is deleted,
§9.2).

**Chat session identity (post-auth):** dashboard chat sessions are keyed by
**opaque local session ids** minted at WS connect — never by the deleted
user ids, which must not survive inside chat state. Reconnect behavior: the
client presents its session id on reconnect and resumes the same session
(fresh id if unknown/expired); two tabs presenting no id get two
independent sessions. Sessions are ephemeral local state — no ownership
semantics, consistent with the no-auth posture.

### 5.2 AgentService

`condor/agents/service.py`:

```
create(spec) / update(slug, patch) / delete(slug)
run(slug, overrides) -> run_id
pause/resume/stop(run_id) / shutdown(slug)   # shutdown is AGENT-scoped (§6.2)
get(slug) / list() / get_run(run_id) / list_runs(slug)
consult(slug, task, context) / delegate(slug, task)
```

Web routes, MCP tools, and control-socket handlers become thin adapters.
`web/routes/agents.py` (1,380 LOC) target: **<10 endpoints**; strategy CRUD
and direct store manipulation move behind the service.

**Pause semantics (defined):** `pause` stops MODEL ticks only — executor
polling, protection barriers, reconciliation, and leases all continue (a
paused run must never mean unmanaged risk). `resume` restarts ticks.

`delete(slug)` is a **tombstone**, not an erase — a recreated agent with
the same slug must never inherit (or launder away) the old one's financial
history:

- rejected while the agent has nonterminal state (running engines, open
  executors, pre-ack SUBMITTING reservations, held leases) **or attributed positions /
  holdings remain** (plain `stop` leaves positions after orders and
  leases are gone);
- RunStore and executor records are preserved permanently;
- the slug is **reserved** — a future `create` cannot acquire the old
  attribution;
- launches and editing are disabled; history/projections stay readable.

### 5.3 AgentSpec (moved into Phase 2 — Phases 3/4 consume it)

The AgentSpec schema, the Agent+Strategy collapse, resolution, and hashing
land HERE, immediately after AgentService — not in Phase 5 — because Phase
3's creation capability carries a frozen spec and Phase 4 writes it into
`run_started`; implementing those against a future type would be circular.
Content (unchanged from the original Phase-5 description):

- Collapse Agent + Strategy (all four current agents are single-strategy):
  strategy body + `default_config` fold into `AGENT.md`. Optional named
  `profiles:` later; no Strategy entity/store/CRUD.
- `run()` freezes one validated spec (model, venue + account resolved to an
  `AccountRef` per §6.2b, schedule, risk, tools, prompt) into the run
  record. Launch overrides limited to `trading_context`, duration, dry-run,
  and **stricter-only** risk (widening rejected).
- **Every AgentSpec declares its `denomination:`** (e.g. `USDC`, `SOL`,
  `USD`) — the numeraire its risk limits are expressed in (owner
  decision). A spec with risk limits but no denomination fails
  validation. §6.1 defines how exposure converts into it.
- **Two hashes:** `source_spec_hash` (authored AGENT.md bytes) and
  `resolved_spec_hash` (frozen effective spec incl. resolved `AccountRef`,
  `default_account` substitution, merged overrides) — a `default_account`
  change alters only the latter; both are stored with the run.
- **Canonicalization (required for a stable resolved hash):** the resolved
  spec is hashed over a canonical typed serialization — UTF-8 JSON, keys
  sorted, no insignificant whitespace, all schema defaults materialized
  (explicit and implicit defaults hash identically), numbers as normalized
  decimal strings, enums as strings, `AccountRef`s fully resolved
  (names → addresses), null fields omitted, credentials never present.
  Test: equivalent specs with different YAML key order, and explicit vs
  implicit defaults, produce identical `resolved_spec_hash`.

### 5.4 Scheduler semantics (unattended operation)

The Scheduler is AgentService-owned. **Phasing note (dependency-correct):
the schedule SCHEMA lands in Phase 2 with AgentSpec; scheduler EXECUTION
and its acceptance tests land in Phase 4 with RunStore** — the scheduler's
boot-state and duplicate prevention read RunStore, which does not exist
before Phase 4. (Until then, agents launch manually.)

- **Agent schedules:** `schedule:` in AgentSpec frontmatter — cron
  expression + optional IANA timezone (default UTC). **A schedule requires
  a bounded duration** (`max_ticks` or a duration field) in the same spec —
  an unbounded looping agent would make every later fire overlap-skip
  forever, so schedule-without-bound fails spec validation. State derives
  from specs + RunStore at boot; no separate agent-schedule store.
- **Routine schedules (durable):** routines are not runs, so RunStore
  cannot restore them. Read-only routine schedules are persisted in
  `store/schedules.json` ({routine, scope, config, cron, tz, last_fired}),
  loaded at boot, same missed-fire (skip) and overlap (skip-with-warning)
  rules. **Write semantics:** serialized writer, atomic tmp+rename with
  fsync, `0700` dir / `0600` file; `last_fired` advances **write-ahead,
  BEFORE spawning the worker** — a crash between advance and spawn loses
  that fire, which is exactly the skip/no-backfill semantics (advancing
  after the spawn could duplicate work instead). **Lifecycle:** deleting
  or tombstoning a referenced routine or its owning agent **disables the
  schedule explicitly** (marked disabled with reason + notification) —
  never a dangling fire, never a deletion blocker. A restart test for a
  scheduled read-only routine is required.
- **Launch rule + fire identity:** a due agent fire calls `run()` normally
  (`kind: scheduled`), and `run_started` persists **`scheduled_for` (the
  fire time) + the schedule/spec identity**; duplicate prevention
  deduplicates on that fire key — never on "last run_started per agent",
  which manual runs and schedule edits would confuse.
- **Missed-run policy: skip, never backfill.** (A trading decision
  computed for a stale moment is wrong, not late.)
- **Overlap rule: skip with warning** if the previous scheduled run is
  still active or its leases have not released. No queuing.
- Tests (§11, Phase 4): restart across a due time (single launch keyed on
  `scheduled_for`, no backfill); overlap fire skipped while the prior run
  holds leases; a manual run between fires does not suppress the next
  fire; scheduled routine survives restart.

### 5.5 No auth (owner decision) — mechanically loopback-only

Single local user; the socket is `0600` (OS-enforced, already done).
Removing auth is only safe if the HTTP surface is **mechanically** local —
today `main.py` binds `0.0.0.0`, which without auth would expose
capital-moving routes to the LAN. **Phasing:** the auth DELETION happens in
Phase 6, strictly after the loopback/CSRF posture below is implemented —
until then the existing login stays. Requirements (implemented with
`condor serve`, Phase 6):

- **Bind 127.0.0.1/::1 by default and reject any non-loopback host** at
  startup (a remote-dashboard deployment is out of scope; if ever wanted it
  comes back WITH auth, not by loosening the bind).
- **Validate HTTP `Host` and WebSocket `Origin`** against a loopback
  allowlist (mitigates DNS rebinding and hostile browser pages driving the
  local API).
- **Every unsafe HTTP method (POST/PUT/PATCH/DELETE) additionally requires
  loopback `Origin`/`Referer` OR the per-process token**: a hostile page
  can submit an ordinary cross-origin form POST to 127.0.0.1 with a valid
  loopback Host, and CORS does not stop the write (today's bodyless
  mutating routes are exactly that shape). The dashboard sends a custom
  header (`X-Condor-Token`, minted per process, delivered with the app
  shell); requests with neither a loopback Origin/Referer nor the token
  are rejected. **`X-Condor-Token` (dashboard CSRF) and
  `store/.direct-token` (condor-direct bootstrap, §6.2) are separate,
  independently generated secrets** — the CSRF token is exposed to
  browser code and must never bootstrap execution authority; neither
  endpoint accepts the other's token.
- Delete `condor/web/auth.py`, the login flow, `frontend/src/pages/Login.tsx`,
  and the `python-jose` dependency.
- Service methods take no principal. **Every agent-initiated mutation —
  agents, memory, skills, routines — derives its target identity from the
  server-side invocation context (the run capability, §6.2), never from a
  caller-supplied `agent_slug` argument** (today's skill/routine tools
  accept arbitrary target slugs; that plumbing is replaced, and the
  existing `scope_gate` becomes a check against the capability's declared
  scope). Cross-agent negative tests exist for each mutating tool family.
  That is capability scoping for agents, not user auth.

**Acceptance:** all surfaces behaviorally identical via the service; no
`from handlers` imports under `condor/`.

## 6. Phase 3 — ExecutionService: durable financial semantics

Nucleus already exists (`executors/ops.py` risk caps + atomic create lock,
`runtime.py` venue-truth reconcile, token blacklist, readiness gate). This
phase completes it:

### 6.1 Risk against full attributed inventory, composed by scope

The pre-trade check prices the agent's whole book as a **disjoint
projection** — the buckets are mutually exclusive so nothing is counted
twice (a naive "executors + inventory + orders" sum double-counts, because
an executor CONTAINS its orders and fills):

1. **pre-ack reservations** — executors in `SUBMITTING` (venue response
   not yet landed), reserved at requested size;
2. **confirmed attributed inventory** — the fold of landed fills
   (`owned_net_base` per instrument, product-specific §6.2);
3. **unfilled risk-increasing remainder** of landed live orders
   (requested − cumulative filled, only for orders that would increase
   exposure);
4. **nothing extra for "a running executor"** — running is a lifecycle
   fact, not exposure.

A partial fill **atomically transfers** exposure from bucket 3 (order
remainder) into bucket 2 (inventory) — total exposure must not jump as an
order moves SUBMITTING → OPEN → partially filled → FILLED (an explicit
lifecycle test). The `snapshot()` (§6.3) is the single computation both
the risk check (via its attribution filters) and the agent prompt consume.

**One unit: the agent's declared `denomination` (§5.3).** SOL, USDC, and
USD amounts cannot be summed, so every bucket converts into the agent's
numeraire before the caps apply, using **fresh adapter-supplied prices**
— and pricing **fails closed**: if a required conversion is stale or
unavailable, the create is REJECTED with a clear error (never priced at
zero, never passed through unconverted). Instruments already quoted in
the denomination convert at 1 (the common case — a SOL-denominated
memecoin agent trading SOL-quoted pairs does no conversion at all).
Cleanup exits under the §6.1 exemption never require pricing (they are
size-based, not value-based), so stale pricing can never trap an agent
in a position.

**`max_open_executors` counts:** every NONTERMINAL executor attributed to
the scope — `SUBMITTING` (pre-ack reservations included) through
`STOPPING/CANCEL_PENDING` — across all of the agent's runs for the agent
cap, within the run for the run cap. Terminal executors never count;
condor-direct (`_manual`) executors never count against any agent.

**Scope composition (all must pass; bucket-1 reservations count in every
applicable scope). Risk limits are owned per agent — there is NO
configurable account-level cap (owner decision):**

| Scope | Cap source | Checked against |
|---|---|---|
| **Agent** | AGENT.md baseline | agent-attributed exposure across all of the agent's runs/delegations/consults |
| **Run** | frozen spec (stricter-only override) | run-attributed exposure |

**Risk-reducing exemption (a prospective predicate, evaluated at
authorization time — not an after-the-fact outcome):** cleanup exits
(`stop --close`, executor barrier closes, shutdown winddowns) are exempt
from the agent/run caps when ALL of:
1. the order **opposes the sign** of the scope's current `owned_net_base`;
2. **reducing capacity is reserved atomically across EVERY nonterminal
   owned opposite-side order in the scope, regardless of label** —
   cleanup exits AND live exit/protection entries in `orders[]`
   (incl. native TP/SL): the aggregate projection (current position plus every
   outstanding opposite-side order fully filled) must reach zero or
   retain the original sign, never cross it —
   `sign(current) × projected_owned_net_base ≥ 0`. A long +10 with a
   resting TP sell-10 has ZERO remaining reducing capacity — a watchdog
   sell-10 must first cancel-and-settle the TP (as stop already does) or
   be rejected; two individually valid sell-6 closes against a long +10
   likewise cannot combine into a short −2. Every close path therefore
   either cancels and settles all owned exit/protection orders first, or
   reserves against them;
3. the **projected fully-filled attributed exposure is strictly lower**
   than current.
Partial fills of such an order are reducing by construction; any
replacement order is recomputed against the LATEST projection and
re-reserves capacity. An agent pushed over its cap by price movement can
therefore always reduce or close. Physical balance/margin, readiness, and
lease checks still apply. This includes the warned opposite-external case
where attributed exposure falls even though the absolute venue net grows.
Concurrent-close races are explicit tests: watchdog vs shutdown,
native-TP vs watchdog, and native-SL vs barrier close — in every pairing
the combined orders never cross zero.

**Risk limits vs venue safety.** The account snapshot supplies **venue
truth** — available collateral/margin, balances, external inventory,
resting orders, lease conflicts — but carries no Condor risk cap. It bounds
trading physically (an order that exceeds available balance/margin fails)
and safety-wise (leases between Condor actors), not by budget.
Concurrent agents on ONE account are **warned/unsupported** under the
cooperative single-actor model (§6.2b) — if the user proceeds anyway, each
agent still enforces its own budget and the lease still prevents
same-instrument collisions, but there is deliberately no joint account
budget. Two agents on **different accounts** of the same venue are fully
supported. A global account budget can be added later if it becomes a
product requirement. Consequently **new accounts need no risk
configuration**: trading happens only when an AgentSpec with explicit risk
limits is launched against the account.

### 6.2 Idempotent creates and order ownership

- **The client-generated `executor_id` is the create identity** — no
  separate intent-id layer. The opener **binds** `executor_id` to the
  immutable owner (run capability or condor-direct owner), the resolved
  `AccountRef`, the executor kind+instrument, and a **canonical
  create-request hash** (same canonicalization rules as §5.3). The record
  is **persisted in `SUBMITTING` before the first venue call**. Replay
  semantics: same `executor_id` + same request hash → the original result
  (idempotency across MCP retries and restarts); same `executor_id` +
  DIFFERENT hash → **rejected** (an id can never be silently rebound to a
  different trade).
- **Creation authority comes from context, not caller fields.**
  `ExecutionService.create` requires a creation context minted by the
  platform; caller-supplied `agent_slug`, `run_id`, or risk limits are
  never authority. **Transport/verification contract:** the context is an
  **opaque capability id** stored server-side, minted by AgentService at
  `run()` and injected into the launched tool session's environment; the
  MCP wrapper passes only that id over the control socket, and
  ExecutionService derives origin, run, agent, `AccountRef`, and risk
  policy **solely from the server-side entry** — never from
  caller-serialized attribution fields (today's env-derived
  `agent_slug`/`agent_id` plumbing is replaced, not trusted). A capability
  is invalidated when its run ends. **Absence of a capability is a
  rejection, not a fallback to `origin: condor`.** The condor-direct
  capability has its own bootstrap: at startup the **persistent runtime
  host** writes a
  per-process **direct-session token** to a `0600` file
  (`store/.direct-token`, rotated every restart); the harness-launched MCP
  wrapper reads it (same OS user = the human) and presents it once to
  register its session, receiving a condor-direct capability whose
  lifetime is **bound to a persistent control connection**: registration
  opens a long-lived socket connection the main process holds; the
  capability is revoked the moment that connection closes (covering a
  wrapper killed without graceful shutdown — SIGKILL drops the socket and
  revokes; explicitly tested) and on process restart (token rotation).
  Short-lived RPC calls reference the capability id; they do not carry
  its lifetime. **Phasing:** this bootstrap ships in Phase 3 as part of
  the persistent runtime host (the interim entrypoint, today's main.py,
  writes the token and holds the registration connections); `condor
  serve` (Phase 6) reuses it unchanged — Phase 3's direct MCP creation
  never waits on the Phase 6 entrypoint.
  Agent tool sessions are spawned BY the main process with their run
  capability injected and the direct token absent from their environment;
  **a session already holding a run capability cannot register or
  downgrade to condor-direct** (rejected and recorded — the escalation
  path, not just malformed ids, is tested). Within the trusted-code
  posture an agent that executes arbitrary code could read the token file
  from disk — the §2 honest-boundary statement covers this; the supported
  surfaces enforce the distinction. Tested: omitted, altered, expired
  (run-ended), and cross-run capability ids are all rejected; an agent
  session presenting the direct token is rejected.
  Two contexts exist:
  1. **Agent run** — capability minted by AgentService at `run()`, carrying
     the frozen AgentSpec + risk policy; creates are risk-gated per §6.1
     and attributed to the run.
  2. **Condor (direct/manual)** — attributed `origin: condor` with no agent
     attribution, and **deliberately not risk-capped** (owner decision: the
     human is the risk authority for their own direct trades). Venue safety
     still applies in full — leases, binding invariant, create idempotency.
     This context covers BOTH origins of direct action: the dashboard
     raw-create button (deferred, not in the initial build) and
     **`manage_executors(create)` from the user's harness chat / condor
     coordinator** — the chat acts for the human, so its direct creates
     carry the same `origin: condor` context and ship via MCP from day one
     (preserving the current test-order workflow). Agent subprocess creates
     are distinguishable (they carry the run capability) and are never
     allowed to masquerade as condor-direct. Read/get/stop/emergency-shutdown
     remain directly available from every surface.
     **Owner schema:** condor-direct records carry
     `owner: {origin: "condor"}` and live under the reserved `_manual`
     partition of the per-slug executor store (a slug no agent can
     claim); the `executor_id` itself is the unique identity — no third
     `operation_id` (there is no group-operation API; if manual
     executors are ever grouped, that operation gets defined with its
     own stop/query semantics first). `run_id`/`agent_slug` are optional
     ONLY for this origin; stop, reconcile, and the lease still have a
     concrete owner to scope to.
- **Market-making mechanism: deferred (owner decision).** The mutating
  `perp_requote` routine is **deleted now** (safekept in
  `~/fengtality/condor-routines/`) — it violated the mutation boundary
  (own credentials, cancel-ALL sweep, venue-net flatten). Until the perp
  market-maker agent is redesigned AFTER this simplification round,
  market making uses **ordinary order executors** through
  ExecutionService: the agent places/cancels resting quotes as normal
  `order_perp` creates and stops, fully owned, risk-gated, and
  reconciled like any other order. The known cost of that interim shape —
  per-quote lifecycle management leaked badly once (15 orphans, 2×
  inventory) when the LLM managed it — is recorded here so the redesign
  starts from that lesson (a deterministic long-lived requote executor
  type remains the leading candidate, but is out of scope for this plan).
  **The shipped perp_market_maker spec is updated in the same change** —
  its AGENT.md/strategy currently REQUIRE the deleted routine and forbid
  ordinary order executors; shipping that contradiction would leave a
  broken product surface.
- **One recovery authority:** executor transitions (including SUBMITTING
  reservations) are
  persisted in the EXECUTOR store (the financial record reconciliation
  reads); the RunStore's `tool_call`/`state_snapshot` events are an
  observability mirror, never a recovery input — no dual-bookkeeping
  ambiguity.
- **Order ownership: persisted executor records are the ONLY authority.**
  Every placement writes its `venue_order_id` (plus the cumulative
  per-order fields, §6.2b) **into the owning executor record before the
  flow advances** — ownership is implicit in the enclosing record's
  run/agent attribution, no separate order store. With per-executor
  client binding (§6.2b, no connector cache) there is no shared connector
  registry to keep consistent: "stop only THIS agent's orders," restart
  recovery, and two agents on different instruments of one account are
  all answered by folding the persisted records.
  **Residual window (accepted, with a resolution path):** a crash after
  the venue accepts an order but before its id is persisted leaves the
  executor `SUBMITTING` with no landed entry. It is **never automatically
  resubmitted**; recovery first queries venue order/fill history and
  otherwise falls to `resolve_submission` (both defined in §6.2b) —
  recovery guarantees apply only once the venue id is durable. Rules:
  - **bulk cancels (stop / shutdown) cancel exactly the ids recorded for
    the scope's executors, never the whole book** (external/manual orders
    coexist untouched);
  - **stop always cancels every persisted order in its scope** — entry
    orders, resting quotes, AND native TP/SL triggers (a detached live
    order would otherwise hold a lease indefinitely or fill with no
    owner). The scope hierarchy is explicit:
    - **stop executor** → that executor's orders;
    - **stop run** (`stop_run`) → every order attributed to that run;
    - **shutdown agent** → every order attributed to the agent (all runs);
    - `--close` closes the remaining signed inventory **within the same
      scope**.
    Closing uses **remaining signed inventory, never gross fills** —
    and the formula is product-specific because fees can be charged in
    the base asset (buying 10 with a 0.01 base fee leaves 9.99 sellable;
    a close for 10 fails):
    - **derivatives (netted):**
      `owned_net_base = Σ gross buy fills − Σ gross sell fills`
      (fees never change a derivative position's size);
    - **spot / prediction holdings:**
      `owned_net_base = Σ gross buy fills − Σ gross sell fills −
      Σ base-asset fees` (quote- and third-asset fees affect performance
      only, reported per asset in `fees_by_asset` — no fill-time quote
      valuation is persisted);
    folded over the scope's order records, recomputed AFTER all
    cancellations settle; the close order is for `−owned_net_base` —
    reversing gross confirmed entry fills would over-close after a partial
    exit, a cancel/fill race, base-asset fees, or offsetting two-sided
    fills. Neither
    variant touches external orders or flattens the account. Lifecycle:
    the **lease is held until every cancellation reaches a confirmed
    terminal state**; if a cancel fails or is uncertain the scope stays
    `STOPPING/CANCEL_PENDING` and restart reconciliation retries it; an
    order that fills during cancellation lands in the cumulative record
    and thus in `owned_net_base` (which `--close` then closes, and plain
    `stop` leaves as an attributed position). Only then is the lease
    released.
- **Leases:** at most one Condor actor per (AccountRef, instrument) — all
  instrument types, one rule (matters most on netted venues, harmless
  elsewhere). "Condor actor" includes BOTH agent runs and `origin: condor`
  direct trades — a direct create on an instrument an agent is actively
  working is lease-rejected (stop the agent first), and vice versa; being
  human does not exempt a Condor-mediated trade from the one-actor rule.
  The warning layer (§6.2b) covers what the lease cannot: **out-of-band**
  activity — trading through the exchange UI or other tools, which Condor
  never sees at order time.
- The LLM permission gate (`risk_gate`) remains approval/dry-run UX only;
  enforcement of record lives here.

### 6.2b Account model: identity, custody, binding, lifecycle

Multi-account per venue is supported upfront (owner decision). The model, in
full, because "check account A, execute account B" is the account model's
worst failure mode and the current code permits it (env-var credential
precedence, ambient per-venue connector clients).

**Phasing (breaks the 2→3 cycle — and activation timing is explicit,
because every phase must boot):** the minimal VenueRegistry
(id → normalization rules), the structured account store schema, and
selector→`AccountRef` resolution land in Phase 2 **as code + fixture
tests, DORMANT** — §5.3's spec resolution and `resolved_spec_hash` need
the types and resolver, but the strict store is NOT the live credential
source yet. Through Phase 2 the system keeps booting and trading on the
existing flat `venues.json` path unchanged (rejection of flat data is
tested against fixtures, not enforced against the live file). **Phase 3
ACTIVATES it**: credential binding switches to the strict store, the
onboarding probe + minimal management UI ship, and the operator cutover
(§12) happens — only from that boot is the flat file rejected. Trading
adapters likewise stay Phase 3 (nothing trades differently in Phase 2).

**Identity.** The **`venue_id` uniquely identifies the venue AND its
deployment**. Mainnet is the default and needs no suffix: `hyperliquid`,
`solana`, `polymarket`. A non-mainnet deployment appends a suffix —
`hyperliquid-testnet` — and is a *different venue*, not the same venue with
a network field. Network is derived from `venue_id`, never stored as
mutable account data; "moving" an address to testnet means selecting
another venue. Within a `venue_id`, accounts are keyed by their
**normalized custody address** (EVM lowercased; Solana verbatim) — the
immutable identity — with a **mutable display name** as metadata. Renames
touch only the display name; AgentSpecs reference the address (accepting
the display name at authoring time, resolved and stored as the address), so
a rename never breaks a future launch.

**`AccountRef = {venue_id, custody_address}`** — network is implied by
`venue_id` (carrying it separately is redundant and, if kept for
readability, must always match the venue definition).

```jsonc
// store/venues.json (sealed fields stay enc:v1: encrypted)
{
  "hyperliquid": {
    "default_account": "0xabc…",
    "accounts": {
      "0xabc…": { "name": "main", "agent_private_key": "enc:v1:…" },
      "0xdef…": { "name": "alt",  "agent_private_key": "enc:v1:…" }
    }
  }
}
```

**Custody identity.** `custody_address` is the address that **holds the
inventory**, resolved by the venue adapter, not assumed to be the signer:
for Polymarket signature types 1/2 it is the funder/proxy, not the signing
key's address. EVM addresses are lowercased for identity comparison; Solana
addresses stay case-sensitive. Leases, inventory, risk, and attribution all
key on the full canonical `AccountRef` — **`(AccountRef, instrument)` is
the one lease key**.

**Binding invariant (ExecutionService).** The caller supplies **only an
account selector** (custody address or display name). ExecutionService
resolves it and binds the executor to the canonical `AccountRef`,
**rejecting** any caller-supplied identity field (`wallet_address`,
`chain_network`, etc.) that conflicts. A create is risk-checked and
executed against the same resolved account by construction — identity
fields never pass through from the model. **Credentials are NEVER written
into the executor config or records** — executor configs are persisted to
JSONL and echoed into application logs, so decrypted material must exist
only inside a runtime `BoundExecutionContext` / connector factory; records
persist the `AccountRef`, nothing more. The
secret-scanning acceptance test covers executor JSONL and application logs,
not only RunStore events.

**Credential precedence.** The sealed store is the ONLY credential source
for trading. Environment variables never override a configured account, and
**startup never reads env credentials implicitly** — the current
`load_hyperliquid_creds` env-over-config precedence is deleted, not
inverted. `condor accounts import-env` exists as an **explicit** one-shot
command that creates an account from env credentials (going through the
same onboarding validation below); there is no implicit seeding and thus no
startup-ordering question.

**Uniqueness.** The `accounts` map is keyed by the **custody address** (for
Polymarket: the funder/proxy, not the signer) within a `venue_id` that
already encodes the network — so duplicate `AccountRef`s are structurally
impossible and credential lookup is never ambiguous. No composite keys, no
extra invariant to enforce.

**No connector cache (owner decision — simpler).** Each executor binds
its OWN client at creation/adoption (inside its `BoundExecutionContext`)
and holds it for its lifetime — construction cost amortizes over the
executor's life, and a handful of executors means a handful of clients.
No registry, no version counter, no eviction: the edit-requires-idle rule
below guarantees no live client ever holds stale credentials, which was
the cache-invalidation machinery's entire job. The "one ambient client
per venue" bug stays fixed — by per-executor binding to a resolved
`AccountRef`, which isolates harder than a keyed cache did. Venues that
need global rate limiting get a small shared per-`venue_id` limiter (a
semaphore, not a client cache). Recovery binds fresh credentials at
adoption.

**Lifecycle (one rule + one warning — most users have one account).**
There is no separate "rotation" operation: accounts are keyed by custody
address, so editing an entry (new signer key, same address) — or even
removing and re-adding it — preserves identity and attribution
automatically. The rules:

- **Editing or removing an account is rejected while it is BUSY — and
  busy is a durable projection, not an in-memory check**: any durable
  nonterminal executor record, unresolved `SUBMITTING` submission,
  held/rebuildable lease, or reconciliation in progress counts (after a
  crash there is no running Python object, but the record still needs its
  credentials for adoption). Account mutations are unavailable until
  startup recovery finishes. This one check is also what makes the
  no-cache model safe: nothing holds a client when keys change.
- **The AccountStore transaction guard spans create REGISTRATION** — the
  existing single guard, no new lock type: `resolve account → validate
  active → bind credentials → persist SUBMITTING opener`, and only then
  release. Without that span, a create could resolve old credentials,
  lose the guard to a credential edit, then persist and trade on the
  stale keys; with it, once the opener exists any edit sees the account
  as busy and rejects.
- **Credential edits revalidate custody identity** — the same derivation
  + read-only probe as onboarding, and the derived custody address must
  EQUAL the map key; a mismatch rejects the edit and requires creating a
  new account entry (this is where Polymarket's signer-vs-funder split
  would otherwise silently rebind an entry to different custody).
- **Removing an account that still has venue state WARNS** (cooperative:
  "Condor will no longer manage these") — covering balances, positions,
  attributed holdings, resting orders, AND triggers; it does not block,
  and re-adding the same address later restores management. If the
  pre-removal venue probe FAILS, removal is still allowed (cooperative
  decision) with the stronger warning "venue state could not be
  verified."
- All edits go through the account store's **single serialized atomic
  writer** (tmp+rename+fsync). Removing the `default_account` just leaves
  the venue with no default; a spec that omits `account` then fails at
  launch with a clear error.

**Executor records: AccountRef is mandatory, no legacy folding.** Every
executor opener MUST contain a valid `AccountRef`; a record without one is
outside the supported schema and folding it raises an explicit
incompatible/corrupt-store error — no inference, no binding events, no
quarantine states, no `bind_executor_account` operation. Old executor
stores are not read by the new product; performance and account attribution
start fresh.

**Venue-truth discovery (no-legacy ≠ ignoring live venue state).** On first
startup — and any startup — for every configured account:

- read venue truth directly (positions, holdings, resting orders,
  balances);
- anything not attributable to a known executor record is classified
  **unattributed external/inherited state**;
- external exposure is not "charged" to any risk cap (there is no account
  cap, §6.1) — its effect is physical: it **reduces the actual available
  balance/margin** new orders can draw on, and it stays visible in
  snapshots/portfolio without being attributed to any agent.

This is current-state discovery, not history migration — it also covers
pointing a fresh Condor at an account that already has positions.

**Cooperative account control (owner decision).** The operating assumption:
**an account hosts one actor at a time — one agent, or manual trading, not
both concurrently**. Alternation is normal (trade manually, then hand the
account to an agent, then back); multiple agents use an account
*sequentially*. The clean handoff is instrument-level: when an agent starts
trading an instrument, that instrument is ideally flat with no manual
resting orders or triggers — then **venue position = agent position**, and
`market_close`, reduce-only TP/SL, venue-truth reconciliation, and
account-wide position reads are all trivially safe for that instrument.
(Resting manual orders and triggers count as manual trading even while the
user is away — they can fill while the agent runs — so the handoff UX
surfaces them.)

Control is **cooperative rather than enforced** — warnings, not blocks.
**Warnings are computed in AgentService/ExecutionService and returned as
structured fields through every surface** (web, MCP tool results, control
socket) — not rendered dashboard-only, since MCP is a primary product
surface and direct creation ships through it. Warned conditions: starting
an agent alongside existing exposure or resting manual orders/triggers on
its instruments; launching another agent on an account one is already
active on; stopping an agent that still has live exposure. **Users may
proceed.** **Timing:** launch-time warnings cover instruments known at
`run()` (genuinely pre-decision — the user can react). For dynamically
selected instruments (scanners), the same-instrument check runs
immediately before the first mutation in ExecutionService, but since it
is deliberately non-blocking and no human is in the loop mid-flow, it is
a **recorded safety notification, not a warning the user can answer
before submission**: it is returned with the create result, emitted to
the channels, and the external-exposure consequence (software exits
instead of native reduce-only triggers, below) is applied
**automatically and persisted on the executor record**
(`warned_external: true`).

The resulting middle ground, stated as rules:

- **Assume users follow the warnings** — the design is sized for the
  cooperative case, not for adversarial co-trading.
- **Keep the same-instrument lease between Condor actors** — agent runs
  AND `origin: condor` direct trades — as inexpensive race protection (one
  Condor actor per (AccountRef, instrument) at a time). The warning layer
  covers only **out-of-band** activity (exchange UI, other tools), which
  the lease cannot see.
- **Persist a cumulative record per LANDED order, folded INTO the owning
  executor record** (no separate order store, no attempt-id layer —
  owner decision for simplicity). `orders[]` contains only
  **venue-acknowledged orders, keyed by their unique `venue_order_id`**:
  `{venue_order_id, client_order_id?, role, side, requested_qty,
  requested_unit, cumulative_filled_base_qty,
  cumulative_filled_quote_qty, fees_by_asset, status, cursor?}`.
  The submission phase lives on the EXECUTOR (persisted in `SUBMITTING`
  before the venue call, `executor_id` as idempotency key): a **rejected
  submission is an executor transition/error, not a fake order record**;
  a landed retry is simply a new entry under its own `venue_order_id`; a
  lost venue response leaves the executor `SUBMITTING` with no landed
  entry — ambiguous, surfaced for manual inspection, never
  auto-resubmitted (exactly the accepted residual window). **No
  pending-submission descriptor is persisted (owner decision)** — the
  intended submission is fully reconstructible from state the executor
  already persists: its config + lifecycle phase + the `orders[]` fold
  (order-kind: the config IS the order; position-kind: OPENING submits
  the entry from config, CLOSING submits `−owned_net_base`, protection
  parameters come from config). The risk reservation for a `SUBMITTING`
  executor sizes from the same derivation. **Generated client order ids
  are omitted for now** (no durable sequence to derive them from —
  accepted); `client_order_id` in `orders[]` remains optional for venues
  that return one. **Ambiguity has a resolution path** — an
  ambiguous `SUBMITTING` executor holds a risk reservation, can block
  account edits/removal, and may hold the lease, so it cannot dangle
  forever:
  recovery FIRST queries the venue's open orders and recent order/fill
  history for the instrument and auto-resolves when the outcome is
  unambiguous (a matching order found → bind its `venue_order_id`; venue
  history conclusively shows nothing landed → mark confirmed-not-landed
  and release). Where venue history cannot establish the outcome, it
  falls to the explicit, fsynced manual operation
  `resolve_submission(executor_id, bind=<venue_order_id> |
  confirmed_not_landed)` — either way the reservation, lease, and
  lifecycle blocks release on resolution. Units are explicit because buys are often
  specified in quote currency while sells use base units (Jupiter swaps
  are input-amount specified), and fees are charged in different assets
  per venue. **Deliberately derived, never stored:** `average_fill_price`
  (= filled_quote / filled_base) and partial-fill status (= OPEN with
  filled > 0). `status` is the minimal enum for a landed order: `OPEN,
  CANCEL_PENDING, FILLED, CANCELED, UNKNOWN` (CANCEL_PENDING is
  load-bearing for the stop/lease lifecycle, UNKNOWN for a landed order
  whose state is temporarily unresolvable). `role: trade | entry | exit |
  protection` is assigned **from the creating executor action/order
  role, not the executor kind** — a standalone single-leg order is `trade`, position legs are
  `entry`/`exit`, native TP/SL is `protection`. Instrument, run, and
  agent come from the enclosing record, which already carries them.
  - **Structure: one uniform `orders[]` collection on EVERY executor** —
    no per-kind shapes. An order-kind executor normally holds one landed
    order (`role: trade`); a position-kind executor accumulates entries
    as legs retry after partial fills or cancellations, distinguished
    only by `role`. Every fold (signed inventory, ownership, reservation,
    performance) runs over the same collection with no branching.
    Terminal and UNKNOWN entries are retained in `orders[]` for
    recovery/performance/audit — only live orders are ever re-placed
    against.
  - **`orders[]` is the SOLE durable financial authority.** The legacy
    aggregate fields on position state — `size`, `entry_price`,
    `amount_spent`, `proceeds`, `open_ref`/close refs — become **derived
    projections (or transitional compatibility properties computed from
    `orders[]`)**, never independently persisted truth; keeping them
    authoritative alongside `orders[]` would reintroduce exactly the
    drift and dual-bookkeeping this schema exists to remove.
  - **Durability: the `orders[]` digest is recovery-relevant.** Today's
    persistence dedups on a recovery key that excludes quantities and
    fees (volatile marks are excluded by design — fills are NOT volatile
    marks, they are financial facts). The recovery key incorporates a
    digest of `orders[]` covering **every recovery-relevant entry
    field** — cumulative quantities, fees, status, AND `venue_order_id`,
    `client_order_id`, `cursor` — so an identifier-only transition (a
    newly landed venue id) is never deduplicated away, and an OPEN order
    that took several partial fills never restarts from stale totals.
  - **Idempotent updates:** polls overwrite with the venue's ABSOLUTE
    cumulative state (cumulative base/quote quantities), never add the latest
    observed delta — repeated polls must not double-count. A venue that
    exposes only fill deltas must make dedup restart-safe: either
    reconstruct the absolute cumulative projection from complete venue
    history on demand, or persist a **compact cursor/seen-fill-ID value
    inside the owning order entry** (the optional `cursor` field) —
    in-memory dedup alone would replay deltas after a restart and
    double-count. Still no separate fill ledger.
  - Ownership queries (stop / reconcile / bulk cancel: "all my open order
    ids") are a fold over the agent's records — exactly what
    reconciliation does today — and stop holds the instrument lease until
    every owned order is terminal (§6.2 stop semantics).
  This is an **aggregate projection updated on poll/fill events, not a
  fill-ledger subsystem** — but it must be this rich: partial fills,
  cancel/fill races, repeated quote replacements, and session
  performance (prices + fees) cannot be reconstructed from "side + filled
  size" alone.
- **Cancel only recorded agent order IDs** — a fold over the scope's
  persisted `orders[]` (§6.2); no in-memory registry is authoritative.
- **Close by placing the order for the scope's remaining signed
  inventory** (`−owned_net_base`, per the product-specific formula in
  §6.2 — gross fills for derivatives, gross minus base-asset fees for
  spot/prediction — never raw gross fills) — never by flattening the
  account.
- **No baseline, adoption, fingerprint, or account-wide attribution
  systems** (considered and rejected).
- **Out-of-band trading during an active agent is unsupported — stated
  honestly**: for netted perps there is no general mismatch predicate (any
  venue net is explainable as the executor's known fills plus an unknown
  external delta), so silent manual interference **may not be detectable**.
  Stand-down fires on the concrete faults that ARE detectable: an owned
  order id that is **unresolvable** — absent from open orders AND from
  terminal order status AND from fill history (disappearing from the
  open-order list alone is the NORMAL fill/cancel transition, which just
  updates the cumulative order record and releases its claim); a forced
  liquidation / margin event; spot or prediction inventory insufficient to
  cover an executor's recorded size. Reconciliation never guesses
  ownership.
- **Native reduce-only triggers are disabled on warned launches.** With
  same-instrument external exposure (user proceeded past the warning), the
  agent's exact-opposite exit can *increase* the account net — a
  reduce-only TP/SL would be rejected by the venue at the worst moment. On
  such launches the executor uses **software-managed exact-opposite exits**
  (the barrier loop's mark-check + close, which already exists) instead of
  native reduce-only triggers, and the warning states that native trigger
  protection is unavailable. Close, stop, watchdog, and crash compensation
  always size from the scope's recomputed `owned_net_base` (after
  cancellations settle, §6.2) — never from raw side + gross filled
  quantity, which over-closes after prior exits.

So proceeding past a warning degrades gracefully (scoped cancels, exact
closes sized from remaining signed inventory, software exits instead of
reduce-only, stand-down on detectable faults) instead of dangerously —
while the undetectable case is documented as unsupported, not papered over.

**Portfolio vs performance (kept deliberately separate).** Two different
questions, two different sources, no reconciliation between them:

- **Portfolio** informs the user: a point-in-time snapshot of what the
  accounts hold (balances, positions, resting orders), read from venue
  truth. Display only — the dashboard's Positions/Portfolio view. Beyond
  reducing available balance/margin, no attempt is made to reconcile
  portfolio contents against executor history or explain where holdings
  came from.
- **Session performance** is tracked from the run's OWN records: realized/
  unrealized PnL, volume, and change in assets over the session, computed
  from the run's executor events. It is never derived from (or checked
  against) portfolio diffs — external transfers, other agents, and manual
  trades move the portfolio without touching a session's performance.

**Inherited inventory (defined):** later runs do NOT implicitly manage a
prior run's inventory. Run-scope risk and `stop_run --close` fold ONLY
that run's own orders — a new run selling against an old run's long would
otherwise appear short in its own projection. Agent-owned inherited
inventory is visible in snapshots (agent-scope attribution) and is
reduced/closed only by: the original executor's own surviving lifecycle
(§4.2), an **agent-scoped** operation (`shutdown_agent --close`), or an
explicit condor-direct disposition. No implicit cross-run allocation.

**Snapshot keying.** Venue truth is account-oriented:
`ExecutionService.snapshot(account_ref, *, agent_slug=None, run_id=None)`.
The account snapshot is the primary object (dashboard enumerates configured
accounts directly — no run required); `agent_slug`/`run_id` are attribution
filters over it. **Physical checks** (available balance/margin) consume the
account snapshot; the **agent/run risk caps** and the agent prompt consume
the attribution-filtered projection (§6.1 — there is no account-level cap).

**Selection UX.** Display names are what users TYPE; the custody address
is what specs STORE. At authoring/save time an `account:` selector
(name or address) resolves immediately and **AgentSpec persists the
resolved custody address** (optionally alongside `account_label:` for
presentation) — `account: alt` never sits in a saved spec to be
re-resolved at every run, so a rename can never break a launch even
BEFORE the agent's first run (tested). Onboarding APIs accept name or
address the same way. Frozen specs and RunStore events store the resolved
`AccountRef` — never credentials. An agent that omits `account` uses the
venue's `default_account`, resolved at `run()` freeze time (see §5.3:
source vs resolved spec hashes — this is the one late-bound selector, by
design).

**VenueRegistry + venue packages (owner decision: user-extensible).**
Canonical `venue_id`s live in a single **VenueRegistry**:
`venue_id → {adapter, deployment/network, address normalization rules}`
(e.g. `hyperliquid`, `hyperliquid-testnet`, `solana`, `polymarket`).
Everything — configs, specs, executor records, leases — resolves
venues through it. Only registered ids are accepted; there is no
legacy-spelling translation layer.

Each venue lives in its **own dedicated package** under `condor/venues/`:

```
condor/venues/
  hyperliquid/   # client, instrument adapters, custody derivation,
  solana/        # onboarding probe, normalization, venue spec
  polymarket/
```

- A venue package exports one **`VENUE` spec** implementing a small
  contract: its `venue_id`(s) (mainnet + suffixed deployments), an
  adapter factory per supported instrument (spot/perp/pred), custody
  derivation from credentials (§6.2b onboarding step 1), the read-only
  probe (step 2), address-normalization rules, **credential-field
  metadata** ({field name, sealed?, description} — what onboarding forms
  render and the sealed store encrypts), and **canonical instrument
  normalization** (`InstrumentRef`: venue aliases for the same market
  resolve to ONE canonical id, so lease keys and attribution can never
  split across aliases). Without those two, "one folder, zero core
  edits" would not actually hold. The registry is built
  at startup by loading the packages under `condor/venues/` — **adding a
  venue = adding one folder that implements the contract**, no core
  edits, which is how users bring their own venues.
- What moves there: today's `executors/hyperliquid.py`, `jupiter.py`,
  `polymarket.py`, the Solana pieces, and the per-venue halves of
  `executors/adapters.py`. The executor core keeps only the
  kind×instrument machinery and the venue-agnostic adapter INTERFACE
  (enter/place/poll/cancel/held_size/custody rules) — the boundary the
  packages implement.
- A **venue conformance suite** (fake-venue fixtures exercising the
  adapter interface: place/poll/cancel, absolute-cumulative updates or
  cursor dedup, custody derivation, probe) is part of the contract — a
  new venue package passes it before the registry accepts it; the suite
  is also the §11 acceptance vehicle (a toy venue package registers and
  conforms end-to-end).

**No legacy compatibility (new-product decision).** `venues.json` accepts
ONLY the structured, account-keyed schema. A flat pre-v1 entry **fails
validation** with a clear "unsupported pre-v1 format" error — there is no
compatibility reader, no automatic conversion, and no startup ordering
between conversion and env import (neither exists). Accounts are created
exclusively through dashboard onboarding, the CLI, or the explicit
`condor accounts import-env` command.

**Onboarding (via the web dashboard) — the COMPLETE account setup path
ships in Phase 3, one phase, not spread across three:** structured
resolution (Phase 2) is config-only; Phase 3 delivers the custody probe,
credential binding, AND a **minimal dashboard/API account-management
surface** (add/edit/remove accounts, pick default) — because Phase 3's
operator cutover requires users to re-onboard, so the setup path must
exist the moment the strict schema does. Phase 6 merely polishes the
Settings area around it. Account creation:

1. derives the custody identity **from the submitted credentials** (never
   trusts a typed address): signer → address, and for venues with custody ≠
   signer (Polymarket funder/proxy) verifies the submitted funder against
   the credentials' access to it;
2. performs a **read-only venue probe** (balance/account query) proving the
   credentials work on that `venue_id` before the account becomes
   selectable;
3. enforces **display-name uniqueness within the venue_id** (normalized
   comparison — names are accepted interchangeably with addresses in every
   selection API, so an ambiguous name is unresolvable).

### 6.3 One portfolio snapshot (account-keyed)

`ExecutionService.snapshot(account_ref, *, agent_slug=None, run_id=None)` —
open orders/executors (typed: pair, side, price, state), attributed holdings (cost
basis), inherited items from prior runs, live venue inventory, recent
terminal events, exposure, PnL. The **account** snapshot is venue truth and
the primary object; the `agent_slug`/`run_id` filters are attribution
projections over it (what the agent prompt and per-run risk consume). The
dashboard enumerates configured accounts directly. The `native_executors`
provider (post-2026-07-15) is the implementation seed; the provider
*selection* logic and both hb-api providers die (§9). The two performance
rollups merge into `executors/performance.py`.

### 6.4 Browser transport is kept

The dashboard cannot speak to a Unix socket. A thin **local HTTP/WS adapter
over ExecutionService** remains (successor of `web/routes/native_executors.py`,
which is already a thin wrapper over `ops.py`): list, performance, get, and
stop. What gets deleted is duplicated business logic, never the browser
transport. **Dashboard raw create is not exposed initially** — the route is
reserved for the later direct-creation UI (§6.2 creation contexts: `origin:
condor`, venue-safe but not risk-capped, already live via MCP for the
harness chat); until the dashboard button ships, browser-side creation
happens only through agent runs.

## 7. Phase 4 — RunStore: one operational history

### 7.1 Format and correctness

One append-only JSONL event stream per run:

```
agents/{slug}/runs/{run_id}.jsonl
```

- **Run identity:** `run_id` is an opaque **ULID**. The `run_started` event
  carries explicit `agent_slug`, `kind` (session|experiment|delegation|
  consult|scheduled — "scheduled" is a schedule-triggered agent run; routine
  executions are not runs), display `seq`, a **frozen AgentSpec + content hash**
  (two hashes, §5.3), and the `AccountRef`s in play (§6.2b). The legacy
  run-id suffix grammars (`_N`, `_eN`, `-dN`, `-cTS`) and
  `slug_from_run_id` are **deleted** — new-product decision: old executor
  logs are not read (§6.2b), so no legacy attribution parser survives
  anywhere.
- **Serialized writer:** one writer task per run in the main process; all
  emitters (engine tick, tool callbacks, approvals, consults, delegations,
  shutdown) enqueue — the MCP subprocess emits via the control socket, never
  by writing files. No cross-process file writes → no locking.
- **Event envelope:** `{v: 1, seq: <monotonic per-run int>, ts, type,
  run_id, tick?, tool_call_id?, executor_id?, payload}` — schema-versioned,
  correlation-id complete.
- **Durability policy:** fsync after financial events (mutating `tool_call`,
  `permission`, `run_started/ended`); buffered flush (≤2s) for the rest.
- **Torn-tail recovery:** a partial final line is ignored on fold; the
  writer truncates it before appending.
- **Permissions:** `runs/` directories are `0700`, event/artifact files
  `0600` — same posture as the sealed store and control socket.
- **Redaction + size caps:** tool payloads pass a redactor (known secret
  patterns, sealed-field names) and a size cap (~16KB/event); oversized
  outputs become markdown artifact files under
  `runs/{YYYY-MM-DD_HH-MM-SS}Z.artifacts/` (dir named for the run's start
  time, decoded from the ULID; files `{HH-MM-SS}Z-{type}.md`, UTC) referenced
  by path+hash. Full ACP payload capture ≠ verbatim credential persistence.

Event types: `run_started, tick_started{prompt_suffix, prompt_sha256},
tick_completed{actions}, tool_call,
permission{pending|approved|denied|timeout_deny|interrupted_void, channel},
state_snapshot, context_changed{learnings, user_memory, skills — baseline at
tick 1, then only on content change},
directive{text, acked}, notification, error, run_ended{status, reason}`.
The frozen prompt prefix is persisted once per run as the `prompt.md`
companion (with a per-tick `journal.md` line as the generated human view);
the exact prompt at tick N reconstructs from prompt.md + last
context_changed ≤ N + the tick's suffix + acked directives, verified by
`prompt_sha256`.

Markdown (`journal.md`, snapshots, experiment/delegation files) becomes a
**generated export** (`condor/agents/exports.py`); nothing parses markdown
back. `JournalManager` shrinks to projections over events. Learnings survive
as curated agent memory (explicit tool, agent-level file) minus the
category/promotion/dedupe machinery; **notes are removed**. Skills remain,
including agent-side skill writing where already supported.

### 7.2 Routines: agent-authorable, strictly read-only

Owner decision: agents **keep** the ability to create and edit routines
(`manage_routines` create/edit stays; `routine_builder` remains the single
authoring entry), and routines are **strictly read-only**: they (a) provide
data that agents/executors consume — market scans, indicators, odds,
inventory reads — and (b) generate reports for humans. **Routines never
execute**: no venue mutations AND no `manage_executors` calls; a routine
that trades (directly or indirectly) is a review defect. This also removes
the creation-context question for routines entirely — a routine never needs
one because it never creates. Routines are trusted code on the user's local
machine — consistent with the no-auth, single-user posture (§5.5).
Guardrails are conventions plus the platform boundary, not a sandbox:

- **Execution stays with executors.** The agent's tick consumes routine
  output (data) and then acts through `manage_executors`/ExecutionService
  itself — the decision-to-trade and the trade both live in the attributed,
  risk-gated path. `perp_requote` (venue-mutating) is deleted outright —
  DONE 2026-07-15, safekept in `~/fengtality/condor-routines/` — and the
  perp market-maker mechanism is redesigned after this simplification
  round (§6.2). The routine worker's contract is stated honestly: the
  **supported RunContext passes no credentials, store keys, execution
  clients, socket paths, or capabilities** — structured inputs in, data
  out — and its environment is scrubbed. But the worker runs as the same
  OS user, so arbitrary trusted code CAN read known filesystem paths,
  import Condor modules, and open the Unix socket; the plan claims no
  filesystem or socket isolation (that would require an OS sandbox or a
  separate user). Read-only remains a trusted-code convention;
  environment-scrubbing and capability absence are what tests verify.
- **Disposable worker subprocess (fault containment, not a sandbox):**
  today authored routines import and run **in-process** under
  `asyncio.wait_for` — a synchronous loop, blocking import, `os._exit`, or
  cancellation suppression can freeze or kill the MCP process, and creation
  even imports the new module during validation *before* any timeout
  applies. Fix: import/validate/run each execute in a short-lived worker
  subprocess that the parent can SIGKILL on timeout. The timeout becomes
  actually hard; a crash takes down only the worker.
- **Authoring is target-scoped by capability, not by argument:** today
  `manage_routines(create/edit)` accepts a model-controlled `agent_slug`, so
  `routine_builder` (empty → unrestricted tool list) can write ANY agent's
  routine directory. Fix: each routine-builder consult/delegation carries an
  **immutable target-agent capability** set by AgentService from the task
  context; the create/edit path writes only to that target and the tool
  argument is ignored/rejected. Cross-agent authoring attempts are denied
  and recorded.
- Routines stay one-shot with a hard timeout (per above); scheduling stays.
- **DONE 2026-07-15:** the ~27 accumulated one-off analysis routines
  (SPCX/USDM/PMM-Mister clusters, incl. the three venue-mutating `usdm_*`
  scripts) were copied to `~/fengtality/condor-routines/` and deleted from
  the branch. The root library now holds the 6 generic primitives
  (`market_scanner`, `arb_check`, `ta_chart`, `price_monitor`,
  `logs_summary`, `pool_report`) + `base.py` infra.
- **Port pass for the survivors (Phase 6 dependency):** the six retained
  primitives still import Telegram types (`ContextTypes` signatures),
  hummingbot clients, and/or `config_manager` — they get the `RunContext`
  signature change (§4.3) and native data sources before those layers are
  deleted. `price_monitor` is currently continuous (long-polling), which
  contradicts one-shot-only: it IS refactored to a one-shot threshold check
  designed to be *scheduled* (the scheduler provides the repetition) —
  decided, not optional.

### 7.3 Legacy history: deleted, not migrated (owner decision)

No export tool, no archive move. Existing `sessions/`, `experiments/`,
`delegations/` directories under `agents/*/` are deleted. **This deletion is
irreversible** — most of this state is gitignored and exists nowhere else
(only some delegation transcripts happen to be tracked); it is accepted as
such, not excused by git history. The old-format parsers —
`sessions_index` markdown/meta scanning, journal section parsing,
`controller_id` compat (17 files) — are deleted in the same pass. The
history UI starts fresh on RunStore events. The executor store likewise
starts fresh under the new schema (mandatory AccountRef, §6.2b); pre-v1
executor logs are not read — live venue state is instead picked up by
venue-truth discovery (§6.2b), which needs no history.

## 8. Phase 5 — explicit MCP tools

(The AgentSpec collapse, resolution, and hashing moved to Phase 2 — §5.3 —
so Phases 3/4 consume an existing frozen spec.)

- MCP surface: retire the `manage_trading_agent` mega-dispatcher for explicit
  tools — `create_agent, update_agent, delete_agent` (tombstone semantics,
  §5.2), `run_agent, get_run, get_agent` (returns the full editable spec),
  `list_agents` (summaries only), one narrowly typed
  `control_run(run_id, verb: pause|resume|stop, close?: bool)` (run-scoped
  verbs), and `shutdown_agent(slug)` (the agent-scoped emergency winddown —
  §6.2's hierarchy makes shutdown slug-scoped, so it cannot hide inside a
  run-keyed tool). Parity across web/MCP/socket is a Phase 2 acceptance
  requirement — the tool list must not silently omit verbs the other
  surfaces have (+ `consult`, `delegate`,
  `resolve_approval`, `get_notifications`, `manage_executors`,
  `manage_memory`, `record_learning` (the §7.1 explicit learnings append),
  `manage_skill`, `manage_routines`, and
  `send_notification` — every shipped AgentSpec declares it, so it stays;
  it **enqueues through the main process over the control socket** (the
  outbox lock is process-local, so subprocess writes would race)). That
  enumerates to **~18 tools** — one page of narrowly typed schemas; the constraint is
  honesty about the count, not squeezing below it.

## 9. Phase 6 — deletions

### 9.1 Telegram (per §4, after §4.1–4.2 replacements are live)

All of `handlers/` (minus §5.1 moves), Telegram parts of `main.py`,
`utils/telegram_*`, `deeplink`, `portfolio_graphs`, `preferences.py`
menu defaults, `python-telegram-bot`, `condor_bot_data.pickle`.

### 9.2 Hummingbot (full removal)

- `mcp_servers/hummingbot_api/` (7.9k).
- `condor/agents/providers/executors.py`, `providers/positions.py`,
  `agents/performance.py` (hb rollup), `bot_name` mode in
  engine/prompts/config, and the **entire `condor/fetchers/` package**
  (it describes itself as Hummingbot API access — not just the three
  named modules).
- Passthrough web routes: `bots.py`, `portfolio.py`, `market.py`,
  `positions.py`, `archived.py`, `controller_performance.py`,
  `backtesting.py`, `executors.py` (hb server-scoped), `servers.py`
  (~3.5k) — and `condor/server_data_service.py` itself plus its
  `ws_manager` topics (SDS is hb-api polling; the dashboard's surviving
  needs read ExecutionService.snapshot and the outbox).
- **The server registry dissolves:** `config_manager` server management,
  `manage_servers` MCP tool, `server_name`/`server_required`/
  `get_effective_server`/`active_server`, engine
  `_get_client`/`_resolve_server` (all live agents are already natively
  serverless). The surviving routines and `routine_builder`'s
  instructions are re-pointed at post-removal APIs (its AGENT.md still
  teaches `config_manager.get_client` today) — acceptance: author and run
  a routine end-to-end using only post-removal APIs.
- Frontend: Bots/BotDetail/Backtesting/Editor/ActiveBots/Archived pages and
  chart components, **plus the surfaces orphaned by other decisions:
  the active auth wiring (login redirects, token plumbing in
  `lib/api.ts`), `CreateExecutor` (raw creation is deferred), and
  `StrategyDetail` (Strategy is removed)**; Portfolio page replaced by a
  minimal Positions/Orders view over `ExecutionService.snapshot` + native
  wallet balances. (Account management does NOT wait for this phase —
  see §6.2b: the minimal setup surface ships with Phase 3's cutover;
  Phase 6 only polishes the Settings area around it.)
- Dependency prune: `hummingbot-api-client`, `geckoterminal-py`,
  `python-jose` (§5.5), and `faster-whisper` (owner decision: no voice
  input in the dashboard). Complete whisper deletion inventory:
  `web/routes/transcribe.py` + its registration in `web/app.py`,
  `utils/transcribe.py`, the voice settings in `web/routes/settings.py` and
  `condor/preferences.py`, and the frontend voice surface —
  `VoiceSettings`, the mic path in `components/chat/ChatInput.tsx`, the
  voice section of `pages/Settings.tsx`, and the transcribe calls in
  `lib/api.ts`.

### 9.3 One process, one model runner

- `condor serve` (single entry): control socket + scheduler + execution
  runtime + web app. `--headless` disables HTTP assets. `main.py` and
  `condor/daemon.py` are deleted.
- **ACP is the only model runner.** `acp/pydantic_ai_client.py` (789 LOC,
  local/OSS models) is **deleted**, recoverable from git history. If local
  models return as a requirement, they return behind the ACP interface, not
  as a second client with duplicate permission/event edge cases.

## 10. Phase 1 (revised) — zero-risk deletions

1. `README.md` product boundary rewrite.
2. Delete `handlers/signals/` (unregistered, zero importers) and
   `handlers/trading_agent/` (empty).
3. Delete `call_main_api` **and update `tests/test_condor_client.py`** (it
   has test references, no production callers).
4. `executors/records.py`: docstring cleanup only (the module is live).
5. Dead frontend components: `pages/Reports.tsx`, `pages/CreateGridExecutor.tsx`,
   `components/routines/{RoutineCatalog,RoutineDetail,CategoryPills}.tsx`,
   `components/bots/CustomInfoEvolution.tsx`.
6. `web/routes/native_executors.py` is **kept** (browser transport, §6.4).
7. **DONE:** one-off routines exported to `~/fengtality/condor-routines/`
   and deleted from the branch.

## 11. Acceptance testing

- **Every phase:** full unit suite green; the current entrypoint boots
  (main.py through Phase 5, `condor serve` from Phase 6); dashboard
  loads; MCP handshake + tool list correct.
- **Network posture (with `condor serve`):** startup with a non-loopback
  bind host is rejected; requests with a non-loopback `Host` header and WS
  upgrades with a foreign `Origin` are refused (DNS-rebinding tests); a
  **cross-origin form POST** to a mutating route (valid loopback Host,
  foreign/absent Origin+Referer, no per-process token) is rejected, and
  the same request with the dashboard's token succeeds.
- **Phase 2:** behavior-parity tests for all lifecycle verbs across
  web/MCP/socket; agent-initiated `scope_gate` still denies undeclared
  system mutations; routine authoring from a consult/delegation targeting a
  DIFFERENT agent's directory is denied (immutable target capability);
  widening risk override rejected; BOTH spec hashes asserted independently
  — `source_spec_hash` matches the authored AGENT.md bytes,
  `resolved_spec_hash` matches the frozen effective spec (only the latter
  changes when `default_account` changes), and equivalent specs with
  reordered YAML keys / explicit-vs-implicit defaults hash identically;
  **schedule schema only**: a valid cron + IANA timezone is accepted, and
  a `schedule:` without `max_ticks`/duration is rejected at spec
  validation (scheduler EXECUTION tests live in Phase 4); **account
  foundation (lands here per §6.2b phasing — DORMANT, fixture-tested)**:
  the structured schema validates and rejects flat pre-v1 FIXTURES,
  selector and `default_account` resolution produce the canonical
  `AccountRef`, duplicate custody keys are structurally impossible, and
  the account-store writer is serialized + atomic — while the LIVE system
  still boots and trades on the existing flat `venues.json` path
  (activation, onboarding probes, credential binding, and the cutover are
  Phase 3).
- **Phase 3:** a duplicate create (same `executor_id`, same request hash)
  returns the original result (fake connector asserts a single venue
  call), and the same `executor_id` with a DIFFERENT request hash is
  rejected; total exposure does not jump as one order moves SUBMITTING →
  OPEN → partially filled → FILLED (disjoint-bucket lifecycle test,
  §6.1); executor JSONL and application logs contain no credential
  material (secret-scan covers them, not only RunStore); kill −9
  **after the venue id is
  durable** with a resting owned order live, then restart → executor
  reconciliation **re-adopts and reconciles to the venue-observed live or
  terminal state** (never cancels by choice — §4.2 survival rule) with
  zero orphan orders and no doubled position; the pre-persist window
  test: an id-less SUBMITTING record is NOT resubmitted, recovery
  resolves it via venue order/fill history where that is conclusive, and
  otherwise `resolve_submission` releases the reservation/lease;
  lease race —
  two concurrent runs on one
  (AccountRef, instrument) → second rejected; the agent-scope risk check
  counts attributed venue inventory + attributed resting orders + pending
  SUBMITTING reservations (test seeds each); frozen
  specs/events contain AccountRefs and never credential material (grep the
  event stream for key patterns).
- **Order projection (§6.2b):** several partial fills while OPEN, then
  process death → restart folds the durably-written cumulative totals
  (never stale); the same absolute poll applied twice does not
  double-count; a delta-only adapter replaying a fill after restart does
  not double-count (persisted cursor/seen-id); a venue REJECTION records
  an executor transition/error and adds NO orders[] entry, and the landed
  retry appears under its own unique `venue_order_id`; multiple
  entry/exit orders fold to the correct signed
  inventory; a spot close after base-asset fees sizes to the NET sellable
  quantity (gross − base fees) and succeeds; legacy aggregate fields
  (size/amount_spent/proceeds) are absent from new records or provably
  derived from `orders[]`.
- **Account model (§6.2b):** two agents on the same venue with different
  accounts run concurrently without lease contention, each risk
  check/snapshot seeing only its own account's inventory; selecting `alt`
  while `CONDOR_HL_*` env vars point at `main` still trades `alt` (explicit
  selection beats ambient env); a caller-supplied `wallet_address`/network
  conflicting with the resolved AccountRef is rejected; two executors on
  different accounts of one venue hold distinct bound clients; Polymarket
  proxy/funder custody address (not the signer) drives leases and
  inventory; renaming an account preserves future AgentSpec launches (specs
  resolve to the address key, names are metadata); editing or removing a
  BUSY account is rejected — including after process death with a durable
  nonterminal record but no running object (removal before executor
  adoption is refused); the create-vs-edit race is closed (an edit
  concurrent with a create either lands before resolution or sees the
  persisted SUBMITTING opener and rejects — no create ever trades on
  pre-edit keys); a credential edit whose derived custody address differs
  from the map key is rejected; after an idle credential edit, a
  relaunched executor's bound client signs with the NEW key; removing an
  account with resting orders (or with a FAILED venue probe) succeeds
  with the appropriate warning, and re-adding the same address restores
  management (address-keyed attribution); env credentials present at startup are NOT read implicitly —
  trading with only env vars set and no configured account fails with a
  clear error, and `condor accounts import-env` creates the account
  explicitly (through onboarding validation); onboarding rejects a typed
  address that does not match the submitted credentials, refuses to enable
  an account whose read-only venue probe fails, and rejects a duplicate
  display name within the venue_id; creating a second account resolving to
  the same `(venue_id, custody_address)` is structurally impossible (map
  key) and an attempted duplicate add errors cleanly; changing
  `default_account` changes `resolved_spec_hash` but not
  `source_spec_hash`.
- **No-legacy schema enforcement (§6.2b):** a flat pre-v1 `venues.json`
  entry is rejected with the "unsupported pre-v1 format" error (never
  auto-converted); folding an executor opener without an `AccountRef`
  raises the incompatible/corrupt-store error (never inferred); a fresh
  structured account onboards end-to-end via the dashboard flow; only
  registered `venue_id`s are accepted anywhere (`hyperliquid` = mainnet,
  `hyperliquid-testnet` = testnet; an unregistered id errors); a toy
  venue package dropped under `condor/venues/` registers at startup and
  passes the conformance suite end-to-end (create → poll → cancel →
  custody derivation → probe) with zero core edits.
- **Venue-truth discovery (§6.2b):** pointing Condor at an account with
  pre-existing positions/resting orders discovers them as unattributed
  external state (visible in snapshots, reducing available balance/margin,
  attributed to no agent) — with zero historical records involved and no
  blocking: starting an agent on an instrument with existing
  exposure/resting manual orders produces a **warning** (proceeding is
  allowed) and such a launch runs with **software exits instead of native
  reduce-only triggers**; an agent's close orders `−owned_net_base`
  (remaining signed inventory, §6.2) and never flattens the venue net
  (external remainder untouched) — tested for a partially-closed position
  followed by `stop --close`, and for offsetting bid+ask fills netting
  near zero; cancel touches only recorded agent order ids, scoped per the
  §6.2 hierarchy (executor / run / agent). **Ownership
  survives crashes:** order ids are persisted before the flow advances, and
  after kill −9 + restart, stop still cancels exactly that agent's orders
  (connector index rebuilt from records). **Stand-down fires on each
  detectable fault** — owned order id UNRESOLVABLE via open orders,
  terminal status, and fill history (a normally filled/cancelled order
  disappearing from open orders is NOT a fault — it updates the cumulative
  record and releases its claim), forced
  liquidation event, spot/pred inventory below an executor's recorded size
  — and the undetectable netted-perp manual-delta case is explicitly
  documented as unsupported (no test pretends to catch it). Launching a
  second agent on an account with one already active produces a warning
  (concurrent agents on ONE account are warned/unsupported, §6.2b); a
  second Condor actor on the SAME (AccountRef, instrument) is
  lease-rejected regardless. Agent stop: cancels ALL of the agent's
  persisted orders including native TP/SL triggers (never external ones);
  `--close` additionally closes the scope's remaining signed inventory
  (`−owned_net_base`, recomputed after cancellations settle); a cancel
  left uncertain (venue error injected) parks the agent in
  `STOPPING/CANCEL_PENDING`, holds the lease, and restart reconciliation
  retries to terminal; an order filled during cancellation lands in
  attributed exposure; the lease releases only after all cancellations
  confirm terminal (second launch on the instrument is rejected until
  then, accepted after).
- **Creation capability:** omitted, altered, expired (run-ended), and
  cross-run capability ids are all rejected by ExecutionService; absence
  of a capability is a rejection, never a silent fallback to
  `origin: condor`.
- **Denomination:** a spec with risk limits but no `denomination` fails
  validation; a create requiring an unavailable/stale conversion into the
  agent's denomination is rejected with a clear error (never priced at
  zero); a SOL-denominated agent trading SOL-quoted pairs converts at 1;
  cleanup exits proceed under stale pricing (size-based, not
  value-based).
- **Risk-reducing exemption:** `stop --close` succeeds while the agent is
  already over its cap (attributed exposure strictly decreases); the
  warned opposite-external case (attributed falls, absolute venue net
  grows) also passes; a risk-INCREASING order while over cap is still
  rejected.
- **Approvals:** resolution mints a one-use grant consumed exactly once
  (blocked call or same-run retry); double-resolve and
  resolve-after-consume are no-ops; **process death with an active run and
  a pending approval** → startup synthesizes `run_ended {interrupted}`,
  voids the approval (`interrupted_void`, notified), and a relaunched run
  must re-request approval under its own create (`executor_id`).
- **Routine containment:** a routine with an infinite synchronous loop (and
  one calling `os._exit`) is killed at the timeout and the MCP process
  survives; validation of a module with a blocking top-level import cannot
  hang the parent; the worker's ENVIRONMENT carries no `CONDOR_*` secrets
  and its RunContext carries no capability or execution surface (what is
  verifiable for trusted code — filesystem/socket isolation is explicitly
  NOT claimed, §7.2); post-Phase-6, a routine is authored and run
  end-to-end using only post-removal APIs.
- **Phase 4:** torn-tail write recovers on fold; concurrent emitters →
  strictly monotonic `seq`; a tool output containing a known secret pattern
  is redacted; >16KB output becomes an artifact ref; **scheduler
  (execution lands here)**: restart across a due time launches at most
  once (deduped on `scheduled_for` fire key, no backfill); an overlap fire
  is skipped with a warning while the prior run is active or holds leases;
  a manual run between fires does not suppress the next fire; a scheduled
  read-only routine survives restart (durable `store/schedules.json`).
- **Phase 6:** a production frontend build succeeds, and every SHIPPED
  AgentSpec parses and completes a dry-run tick against the post-Phase-6
  tool surface (no spec may reference deleted routines/tools — the
  perp_market_maker spec is the known case); grep gate — no `telegram`,
  `hummingbot_api`,
  `get_bots_client`, `bot_name`, `server_required`, `active_server`,
  `config_manager`, `ServerDataService`, `jose`, `whisper`, `transcribe`,
  `VoiceSettings`, `getVoiceSettings`, or `created_by` references left in
  live code; approvals
  round-trip works end-to-end from a harness (pending → channel event →
  `resolve_approval` → recorded permission event).
- **Live smokes:** dry-run smoke = one **experiment** tick that plans and
  journals but places nothing (asserted zero venue calls). A separate,
  explicitly opt-in live canary (world_cup, one $10 resting limit,
  immediately cancelled) validates the real venue path after Phases 3 and 6.

## 12. Sequencing summary

Pre-Phase-1 gates:
- The account-model decisions in §6.2b (binding invariant, explicit-only
  env import, per-executor client binding, custody identity, address-keyed
  accounts, edit-requires-idle lifecycle, venue-truth discovery) are
  settled ABOVE —
  they are design prerequisites the execution phases implement, so Phase 3
  cannot start until they are reflected in code review checklists.
- **DONE 2026-07-15:** the 17 runtime review fixes were committed as their
  own reviewed unit with a green 448-test run (commit 7837472) — not
  bundled into Phase 1.

**Persistent-runtime-host startup ordering (required — interim main.py
from Phase 3, `condor serve` from Phase 6). Two sequences, because
RunStore and the scheduler do not exist until Phase 4:**

*Phase 3 (interim):*
1. acquire the singleton — control socket bind (ping-before-unlink);
2. legacy interrupted-session sweep (the existing mechanism — RunStore
   does not exist yet);
3. rebuild leases from executor records; reconcile executors and accounts
   against venue truth (venue-truth discovery, §6.2b);
4. ExecutionService readiness opens (mutating MCP/web calls 503-gated
   until here). No scheduler exists to enable.

*Phase 4 onward:* step 2 becomes RunStore recovery (synthesize
`run_ended {interrupted}` for orphaned streams; void their pending
approvals as `interrupted_void`), and a new step 5 enables scheduled
fires last — a fire that came due during a slow startup follows the
no-backfill rule (skipped, not launched against half-reconciled state).

Phase 3 operator cutover (deliberate, one-time): the strict schema means
the first boot after Phase 3 **rejects the existing flat `venues.json`** —
the operator re-onboards accounts (dashboard or `condor account
import-env`) and the executor store starts fresh; running agents must be
stopped flat before the upgrade. This is the no-legacy decision surfacing
as a planned step, not an accident to be discovered in production.

| Phase | Content | Risk | Payoff |
|---|---|---|---|
| 1 | boundary + corrected dead-code list (+ routines export: DONE) | none | clarity |
| 2 | AgentService + AgentSpec (§5.3, incl. schedule schema) + minimal VenueRegistry / account store / selector resolution (§6.2b phasing) + core out of handlers/ | low | one service + one config owner, unblocks 3/4 |
| 3 | ExecutionService: idempotent creates, order ownership, leases, snapshot, browser adapter, venue packages (`condor/venues/`), account onboarding + minimal management UI | medium | durable financial semantics, user-extensible venues |
| 4 | RunStore (hardened) + scheduler execution (§5.4) + legacy formats deleted | medium | one history format, unattended operation |
| 5 | explicit MCP tools | low | better model schemas |
| 6 | delete Telegram + Hummingbot + auth + pydantic-ai; `condor serve` | low (after 2–5) | ~100k+ LOC gone |

Dependency ordering: the **notifications** replacement (§4.1: outbox
channels) lands in Phases 2–3; the **approvals** replacement (§4.2) depends
on durable `permission` events and therefore lands **with Phase 4**
(RunStore). Phase 6 (Telegram deletion) requires both to be live.
