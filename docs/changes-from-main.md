# Changes from `main` — the harness + agent-framework branch

What `spike/simpler-agent-framework` changes relative to `main`
(merge-base `877bb09`, the FEAT-003 era), consolidated from the
per-refactor design documents this file replaces. The as-built
architecture is described in
[simpler-agent-framework.md](simpler-agent-framework.md); this document
explains **what changed and why**. ~52 commits, ±22k lines, two phases:
the MCP harness-validation spike, then the agent-framework refactor
series built on top of it.

## 1. MCP harness validation (the base spike)

`main`'s only way to talk to Condor was its own Telegram/web chat. The
spike proved the Tier-1/Tier-2 split live: **Claude Code and OpenClaw
drove the same running Condor process** via `mcp_servers/condor`,
including cross-harness handoffs (start a session in one harness, stop
it from another) — and fixed the three bugs that surfaced. Identity
became explicit: the MCP server **fails fast** when it has no identity
(was: an opaque 403 deep in the first call), and **Tier-A auto-bind**
resolves a missing identity to the sole approved user on single-user
installs (the OS user launching the harness already owns every secret
on disk; with zero or multiple approved users nothing is bound and the
caller is told exactly what to pass).

## 2. The agent model, rebuilt

### One history per agent; strategies are playbooks

`main` grew per-strategy trees — each strategy owned its own sessions,
learnings, and config, so an agent's track record was fragmented and
A/B-testing two playbooks meant two histories. Now: **the agent is the
unit of identity and accumulation** (`agents/{slug}/`), a strategy is a
flat playbook file (`strategies/{sslug}.md` — tick tactic +
`default_config`, nothing else), and which playbook a session ran is
session *metadata*, not part of its address. Session ids are
`{agent_slug}_{N}`; running two strategies concurrently is just two
sessions of one agent.

### One execution primitive under a policy lattice

Every brain invocation goes through `run_agent`
(`condor/agents/run.py`) parameterized by a permission policy
(`policies.py`): `human_gate` (consults — Telegram Approve/Reject,
fails CLOSED to `deny_gate` when no chat is available; a `None`
callback meant auto-approve and was a live bug class), `risk_gate`
(anything that can trade — one shared policy, seeded per caller), and
`AUTO` (serverless specialists). Delegations became **risk-gated
background runs** with a per-call `risk_limits` override that REPLACES
the agent baseline. Latest tightening: a server-backed agent **cannot
be saved without a risk baseline** (`AgentStore._save`) — the AGENT.md
defines what the agent does, including how much it may do unattended;
`{0, 0}` is the explicit read-only statement.

### Four primitives, four artifacts

`main`-era code stored every invocation as a "session" with a `kind`
tag (tick_loop / delegation / consult), and the tag infected every
list, filter, and API. Now each verb leaves the artifact its shape
deserves:

| Verb | Runs | Artifact |
|---|---|---|
| consult | one, synchronous, human-gated | none — the answer is the artifact |
| delegate | one, detached, risk-budgeted | flat transcript `delegations/{date}-dN.md` |
| experiment | ticks with ALL mutations cancelled | flat snapshot `experiments/{date}-eN.md` |
| session | scheduled ticks engaging capital | `sessions/session_N/` — journal, snapshots, frozen config, track record |

"Dry run" was unified to **experiment** everywhere in code and docs
(the term survives only as a user synonym in `CONDOR.md`; `dry_run:
true` still translates). Migrations ran with backups
(`agents.pre-01b.bak/`, `agents.pre-07.bak/` — delete once trusted).

### The assistants layer dissolved

`main` had a generic multi-assistant system (`assistants/` +
mode-picker machinery) with exactly one tenant. The chat brain moved to
repo-root `CONDOR.md` (a deliberately non-host-owned filename), the
chat store to root `store/`, and the mode machinery
(`discover_assistants`, `AGENT_MODES`, pickers, the threaded `mode`
value) was deleted — which also fixed the Telegram path injecting the
brain twice. The repo root now mirrors an agent-home
(`CONDOR.md + skills/ + routines/ + store/` ↔ `agents/{slug}/…`),
backup at `assistants.pre-06.bak/`.

## 3. Skills: one portable format, two estates

All skills became agentskills.io-conformant (hyphenated names,
single-line frontmatter, routing trigger folded into `description`,
Condor extras as flat `condor-*` metadata). **Host-facing** skills
("how to drive Condor via MCP") live once at repo-root `skills/` and
serve four consumers from one directory: Condor's own chat, Claude Code
(`.claude/skills` symlinks), OpenClaw (workspace scan), Hermes (tap
layout). **Agent-internal** skills live behind the MCP boundary in two
tiers — `agents/{slug}/skills/` (local) over `agents/_shared/skills/`
(chat-writable only; agents get a loud read-only error). Duplicated
knowledge was consolidated (shared executor-mechanics skill, canonical
pmm parameter doc that had drifted from reality).

An **automatic curation loop** (agents delta-patching their own skills
at session end, under a restricted tool profile) was designed,
implemented, live-validated — and then **removed as over-complex**,
taking the whole MCP tool-profile mechanism with it. What remains is
deliberate: in-run capture to `learnings.md` (capped, deduped,
provenance-prefixed) plus human-directed `manage_skill(action="patch")`
and `promote_learning` in chat.

## 4. Decisions recorded without code

- **Notifications are Telegram-first.** A full notification bus
  (persisted inbox, web/webhook/desktop sinks, an MCP mailbox) was
  designed and then deliberately **not built**: with Telegram as the
  spine and Hermes-class harnesses being Telegram-native, the bus is
  maintenance without benefit. One real bug remains open by decision:
  runs launched from the web (`chat_id=0`) notify no one — the fix is
  to resolve the TG chat from `user_id` (in DMs `chat_id == user_id`).
- **Dropping Condor's own harness for Hermes was rejected.** Evaluated
  seriously (Hermes is MIT, multi-platform, MCP-capable) and turned
  down: it trades away the approval surface, the onboarding funnel, and
  the in-repo reference harness. The standing posture: **no harness is
  privileged** — Condor's chat is the batteries-included default, and
  external harnesses are peers over MCP.

## 5. Onboarding by harness selection

Installing Condor no longer presumes Telegram. Modeled on a field study
of the OpenClaw and Hermes installers (both: bash bootstraps, the
product CLI asks the questions over `/dev/tty`):

- **`install.sh`** — bootstrap only: git/uv, clone-or-update, `uv
  sync`, `.env` template, hummingbot-api probe; `--stage-json` emits
  machine-readable stage lines so an agent can drive the install.
- **`condor init`** (`python -m condor.cli`, `make init`) — idempotent
  owner of every product question: the identity anchor ("Do you use
  Telegram?" → TG id, else a minted integer; multi-user disables
  auto-bind), a loud hummingbot-api probe, and a harness multi-select
  (Condor's TG+web harness bundled and default-selected; detected
  external harnesses get config **emitted, never installed**).
- **`condor login-token`** — a stateless, purpose-tagged, 5-minute JWT
  accepted by the token-login route, so Telegram-free installs can
  reach the dashboard. `decode_jwt` rejects purpose-tagged tokens as
  session bearers (HTTP and WS both).

User-facing walkthrough: [installing.md](installing.md). Still external
to this repo: serving `install.sh` from condor.hummingbot.org and
pointing `hummingbot/deploy`'s setup.sh at it.

## 6. Validation

273 tests pass (`.venv/bin/python -m pytest`); frontend `tsc` clean.
Live end-to-end runs through the real server validated the new schema:
experiments self-stopped and wrote `{date}-eN.md` flat files, a
strategy guardrail aborted correctly on a missing pair, and a full
STAND-DOWN tick ran with regime analysis; delegations and migrated data
were verified through the REST API. Risk baselines were set for every
trading agent (`revival_trader` {500, 5}, `funding_rate_watcher` {0, 0}
read-only, `mm_expert` {600, 10}).

## 7. Deliberately deferred

Aggregate exposure cap across concurrent sessions of one agent (each
session budgets alone; the platform-side deploy loss cap is the
cross-cutting bound) · the `chat_id=0` notification fallback (§4) ·
`journal.py` split · a `consults.log` · TickEngine rename · deleting
the migration backups (`agents.pre-01b.bak/`, `agents.pre-07.bak/`,
`assistants.pre-06.bak/`) once the new layout is trusted · hosting
`install.sh` on the docs site.
