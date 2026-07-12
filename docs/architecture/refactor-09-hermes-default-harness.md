# Refactor 09 — drop Condor's own harness: Hermes as the default Tier 1

Status: **proposed** (2026-07-12), gated on a Hermes validation spike (§7).
Extends the Telegram-first decision (refactor-08 §8) to its radical
conclusion: Condor stops maintaining an interactive chat harness at all.
Condor = the trading backend (Tier 2) + skills tap + monitoring dashboard;
**Hermes is the conversation.**

## 1. Why this is architecturally cheap to say

The three-tier split already did the hard work. The harness spike proved
Tier 1 is swappable *live* (Claude Code and OpenClaw both drove the same
running Tier 2; sessions survived harness swaps mid-flight), and every gate
that matters is **Tier-2-side**: `human_gate`, `risk_gate`, the delegation
baseline rule, and the tick engine all live in Condor's persistent process
and are indifferent to which harness invoked them. Condor's own Telegram
chat and web chat are just two more Tier 1 clients — ones we happen to
maintain ourselves.

## 2. What "our harness" actually is (the deletion candidate)

~3,900 lines of Tier 1 code, none of it the moat:

| Surface | Code | Notes |
|---|---|---|
| Telegram chat sessions | `handlers/agents/__init__.py` (~900), `session.py` (334), `stream.py` (342), `menu.py` (185) | message handler, session lifecycle, compaction, streamer, menus |
| Model picker | `openrouter_models.py` (119) + menu wiring | chat-only; agents set `agent_key` in AGENT.md |
| Web chat | `chat_ws.py` (468), `transcribe.py` (68), `ChatPanel`/`ChatInput`/`ChatMessage`/`useChatSocket` (~1,490 TSX/TS) | the dashboard's chat panel |
| Chat brain | `CONDOR.md` + `_load_condor` + `build_initial_context` chat parts | routing/memory/skills instructions duplicated with the MCP server-instructions block |

**Explicitly NOT deletable:** the ACP + pydantic-ai client stack
(`condor/acp/`, ~1,500 lines). It powers the *agents' own brains* — every
tick, consult, and delegation spawns one of these clients from `run_agent`.
It stays regardless of who owns the conversation.

## 3. What Hermes provides (verified against their docs, 2026-07)

- **MIT license** — forkable; dependency risk is pinnable, not existential.
- **Multi-platform gateway**: "Telegram, Discord, Slack, WhatsApp, Signal,
  and CLI — all from a single gateway process", plus a full TUI. Telegram
  flagship = exactly our notification spine (refactor-08 §8).
- **Any model**: "Nous Portal, OpenRouter, OpenAI, your own endpoint" —
  `hermes model` switches without code. Replaces our AGENT_OPTIONS picker,
  OpenRouter pagination UI, and per-CLI ACP bridge maintenance *for chat*.
- **MCP integration**: "Connect any MCP server for extended capabilities"
  — the one sentence this whole proposal rests on; depth unvalidated (§7).
- **Security**: command approval, DM pairing, container isolation — but
  their guardrail culture is documented (refactor-05 §2.1) as **opt-in and
  leaky by default**; approval posture must be verified, not assumed.
- **Cron scheduler** with delivery to any platform (their side-channel for
  reports; Condor's TickEngine remains the risk-gated execution loop —
  these do not compete).
- **Skills taps + memory**: our repo-root `skills/` IS the tap layout
  already (`hermes skills tap add <condor-repo>`); their episodic/semantic/
  procedural memory triple mirrors ours.

## 4. What gets deleted, what gets kept

```
DELETE (after §7 validation, staged per §8)
  Telegram chat sessions + compaction + streamer + menus + voice path
  Web chat panel + chat_ws + transcribe
  OpenRouter picker UI
  CONDOR.md chat brain -> folded into the MCP server-instructions block
                          (one routing text instead of two)

KEEP — Condor's actual product (Tier 2 + surfaces no harness replaces)
  TickEngine, sessions/experiments/delegations, risk lattice, journals
  run_agent + ACP/pydantic-ai clients (the agents' brains)
  condor MCP server + hummingbot MCP (the product API)
  Web dashboard, monitoring-only (agents, track records, portfolio)
  A SLIM Telegram bot: notifications, human_gate Approve/Reject buttons,
    /login token minting. (/delegations-style queries move to Hermes via
    the existing delegate(action="list") tool.)
```

## 5. Design decisions this simplifies (their framework vs ours)

1. **Notifications** — already decided (refactor-08 §8): no layer; the
   Hermes chat and the Condor ping chat share a phone.
2. **Multi-model chat UX** — model switching, streaming, compaction,
   interrupts, voice: all Hermes roadmap, zero Condor code. We keep model
   plumbing only where it earns money (agent ticks).
3. **The two-brains problem** — today the routing knowledge lives twice:
   CONDOR.md (our chat) and the MCP server instructions (external hosts).
   With one harness class, one copy survives.
4. **Per-host QA matrix** — Hermes becomes the first-class tested harness;
   Claude Code/OpenClaw demote to "spec-conformant, best effort" (the
   spike already proved the mechanics there).
5. **Identity** — a Telegram user id is stable across bots: the id Hermes
   sees IS Condor's `user_id`. No mapping layer, and web login's
   TG bootstrap stops being an oddity — it's the platform.
6. **Web auth surface** — the dashboard stops needing chat-grade WS
   session/permission plumbing; monitoring reads + a few POSTs.
7. **Skills distribution** — the tap becomes the primary channel; the
   `.claude/skills` symlinks and OpenClaw scan remain free byproducts of
   the same directory.

## 6. What we lose, honestly

1. **The approval surface for raw dangerous tools.** Our chat confirms
   `place_order`/executor mutations via DANGEROUS_TOOLS + TG buttons.
   Hermes drives the hummingbot MCP directly; protection becomes *their*
   command-approval posture — from a project whose guardrails default off.
   Mitigations, in order of strength: (a) ship a recommended Hermes config
   with approval ON for the trading tools; (b) route users toward
   consult/delegate (Condor-side gates) via the server instructions — the
   routing rule already says this; (c) if the spike shows auto-approve
   defaults, add a server-side confirm (TG button round-trip) on
   `place_order`-class tools before making Hermes the default. (c) is the
   only new code this proposal might *add*.
2. **The onboarding funnel.** First-run becomes "install Hermes, tap
   Condor, add the MCP server" — our product's front door is another
   project's installer. Mitigation: a `condor init --hermes` that writes
   the Hermes config; the dashboard stays ours.
3. **UX control.** Formatting, streaming quality, voice notes on Telegram:
   Hermes's decisions now. MIT means we can patch, but every patch is a
   fork cost.
4. **A tested in-repo reference harness.** Today our own chat is the
   always-available integration check. Post-deletion, the validation
   burden shifts to the Hermes QA matrix in CI-less form (manual, like
   the harness spike).

## 7. The gate: a Hermes validation spike (do this first)

The harness spike deliberately skipped Hermes ("not installed, not
attempted"). Before *default*, repeat the exact OpenClaw/Claude Code
matrix on hermes-agent:

1. Connect condor + hummingbot MCP servers (stdio config) — does
   `list_agents` hit the live Tier 2?
2. `start_session` / `start_experiment` / consult / delegate / stop from
   Hermes chat; cross-harness handoff (start in Hermes, stop from TG).
3. **The approval test (the go/no-go):** call
   `manage_executors(action="create")` raw from Hermes — what does the
   user see? Approval prompt = go. Silent execution = blocker until §6.1c.
4. `hermes skills tap add` on the repo; `/agent-builder` invocation;
   confirm Skills Guard passes and the skills route to MCP tools.
5. Identity: Tier-A auto-bind from Hermes's spawned MCP subprocess.
6. A week of dogfooding as the daily driver.

## 8. Staged path (never delete first)

1. **Validate** — §7 spike; fix what it surfaces.
2. **Default** — README/onboarding lead with Hermes (`condor init
   --hermes`); Condor chat still present.
3. **Freeze** — Condor TG chat + web chat panel enter maintenance mode
   (bug fixes only, banner pointing at Hermes).
4. **Delete** — after the freeze survives real usage (suggest 4–6 weeks),
   remove §2's surfaces; slim the TG bot to
   notifications/confirmations/login; fold CONDOR.md into the server
   instructions.

The freeze step is what makes this reversible: if Hermes stalls or the
approval posture disappoints, we un-freeze a working harness instead of
resurrecting a deleted one.

## 9. Variant considered: our own UI as a TUI, Telegram demoted to optional

Question raised against this proposal: if we keep any surface of our own,
should it be a TUI instead of the slim Telegram bot? Standing assumption:
condor + hummingbot-api run on the same box (local, or hosted with SSH).

### 9.1 What a Condor TUI is — and is not

It is **not a chat harness.** Hermes owns the conversation (that is this
whole proposal), and Hermes already ships its own TUI and CLI modes — a
Condor chat TUI would be rebuilding §2's ~3.9k lines in a different
medium, competing with the harness we just adopted. It **is an ops
panel**: k9s-for-agents. Pending approvals, a notifications tail,
sessions/experiments/delegations with status, start/stop, log tail — all
reads the existing REST API; only approvals and the inbox need anything
new.

### 9.2 Scoring the slim TG bot's three jobs under the same-box assumption

§4 kept Telegram for exactly three jobs:

| Job | TUI replacement | Verdict |
|---|---|---|
| Async pings (delegation done, tick error) | none — a TUI is pull-only; it informs only while attached | TG (or a webhook sink) still needed for away-from-box; the phone is the point |
| `human_gate` Approve/Reject | approvals queue + keypress; timeout → deny (fail closed, the `deny_gate` precedent) | works attached; detached = deny or phone |
| `/login` token minting | print the login URL to the terminal — same box, trivial | TUI wins outright; TG-as-IdP was already softened by Tier-A auto-bind |

### 9.3 The Hermes twist: where the user runs Hermes decides

The refactor-08 §8 argument for Telegram-as-spine was that the Hermes
conversation and Condor's pings share a phone. That holds only when the
user runs Hermes **on Telegram**. If they run Hermes's TUI/CLI on the
same box, everything is already terminal-local and our TG bot becomes
the odd surface out — a Condor TUI is the natural sibling pane. Both
deployments are real, so the answer is not *replace*, it is **demote**:

- **TUI = the default, zero-config surface.** First run needs no bot
  token, no chat id — `condor tui` works the moment the process is up.
  This also softens §6.2 (onboarding through another project's door).
- **Telegram = an optional sink**, enabled by one `.env` line, for
  anyone who wants pings and approvals on a phone — which includes
  everyone running Hermes-on-Telegram.

### 9.4 The honest cost: it resurrects part of refactor-08

Refactor-08 §8 skipped the notification bus *because* TG was the
mandatory spine. Making TG optional revives the minimal core of it: a
persisted notifications inbox, and a pending-approvals queue —
`confirmation.py`'s in-memory `_pending` futures must become answerable
from any surface, not just a TG callback. Roughly two small tables + a
queue API + a Textual app (~600–1,000 lines). Refactor-09 as written is
pure deletion plus a bug fix; this variant adds a modest build back.
(The mailbox and webhook/desktop sinks stay skipped.)

### 9.5 The convergence that can make it free

§6.1's mitigation (c) — a server-side confirm on `place_order`-class
tools if the spike shows Hermes auto-approves — needs **the same
pending-approvals queue**. If the spike forces (c), the queue gets built
regardless, the TUI is merely its cheapest first surface, and the same
tables later back an approvals/inbox view in the kept web dashboard
(covering hosted boxes without SSH). It also fixes refactor-08 §1's
wrong-surface confirmation: a consult launched from Claude Code stops
sending its buttons to a phone nobody is holding.

### 9.6 Recommendation and sequencing

1. Run the §7 spike first — the go/no-go is unchanged.
2. **If the spike forces server-side confirm**: build the queue + inbox,
   ship the TUI as their first surface, demote TG to an optional sink.
   The variant costs almost nothing extra on top of code we must write.
3. **If Hermes's approval posture is solid**: the TUI is optional polish;
   keep the slim TG bot per §4 and revisit only if TG-free onboarding
   becomes a goal.

Either way §2's deletion list is untouched — the TUI never replaces the
conversation, only the pager.
