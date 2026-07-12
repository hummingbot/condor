# If any harness can consume Condor's MCP server, what's actually defensible?

Companion to [`docs/architecture/agent-framework.md`](../architecture/agent-framework.md),
[`docs/strategy/business-strategy.md`](./business-strategy.md) (especially
§10's moat analysis), and [`docs/strategy/fork-vs-build.md`](./fork-vs-build.md).
This doc answers a sharper version of that last doc's question: if Claude
Code, Cursor, Hermes-agent, OpenClaw, or any other MCP-capable harness can
already talk to Condor's own MCP tool server directly — bypassing Condor's
bespoke chat/session layer entirely — should Condor just focus on that tool
layer and publish instructions for using it with any harness? And if the
chat harness *and* the execution layer underneath (Hummingbot today, maybe
CCXT or a next-gen Hummingbot tomorrow) are both swappable, what is Condor's
actual defensible IP?

**Short answer**: yes, any MCP-capable harness can consume Condor's tool
server today, with almost no new engineering required to document it. But
the honest boundary is narrower than "just ship the MCP server": a
meaningful slice of what makes Condor useful (`consult`/`delegate`, most of
strategy management) depends on Condor's own backend process being alive,
not just the MCP subprocess. Once that boundary is drawn correctly, the
defensible layer turns out to be the backend/strategy runtime and the
accumulated content and track record riding on top of it — not the chat
harness, and not the tool-wrapping glue around the execution layer, which
should be treated as commodity regardless of this question.

## 1. Confirming the premise: yes, any MCP harness can consume Condor's server

This isn't hypothetical — it's already how Condor's own runtime works
internally, and the mechanism is fully generic:

- `mcp_servers/condor/server.py` builds a `FastMCP("condor", ...)` instance
  and runs it with `mcp.run()` (`server.py:607-608`, `__main__.py:1-3`) —
  with no transport argument, that's **stdio**, the same transport any MCP
  client (Claude Code, Cursor, Gemini CLI, Hermes-agent, OpenClaw) already
  knows how to launch and speak to.
- The repo's own root `.mcp.json` already registers it exactly the way an
  external harness would: `"condor": {"type": "stdio", "command": "uv",
  "args": ["run", "python", "-m", "mcp_servers.condor"], "env": {}}`. A
  bare `claude` CLI session opened in this repo — or any other MCP client
  pointed at the same command — already auto-discovers and can call it
  today, with zero new code.
- The two other registered servers (`mcp-hummingbot`, `playwright`) follow
  the identical `{name, command, args, env}` stdio shape — this repo
  already treats "stdio MCP server description" as a uniform integration
  surface across in-house and third-party servers alike. There's nothing
  Condor-specific about the transport or registration mechanism to work
  around.
- Every tool (`manage_memory`, `manage_skill`, `manage_routines`, `consult`,
  `delegate`, `manage_trading_agent`, ...) is an ordinary `@mcp.tool()`
  async function — typed input schema in, dict out. None of them requires
  a Condor-specific session or agent object to exist as a precondition of
  being *callable*. Any MCP client can issue the call.

## 2. The nuance that matters: "the MCP server" isn't the whole boundary

Not every tool works the same way once called, and the difference is
load-bearing for this decision:

- **Genuinely self-contained** (filesystem/local-DB only, no other Condor
  process required): `manage_memory`, `manage_skill`, `manage_routines`.
  These work against `MemoryStore`/`SkillStore`/`StrategyStore` directly,
  inside the MCP subprocess itself. An external harness pointed only at
  this server, with no other Condor process running, gets full, real
  functionality here.
- **Depend on Condor's own backend web API being alive** at
  `127.0.0.1:{WEB_PORT}`: `consult`, `delegate`, and several
  `manage_trading_agent` actions. These are thin JWT-authenticated proxies
  (`condor_client.py`) into Condor's main process — by design, per that
  module's own comment: *"TickEngines must be created in the main process
  so they survive beyond the MCP subprocess lifecycle."* Point an external
  harness at a standalone MCP subprocess with no backend running, and these
  calls fail with an explicit `APIError` ("Failed to reach main process
  API"), not a silent degradation.
- **Identity is optional but consequential**: `CONDOR_CHAT_ID`/
  `CONDOR_USER_ID` default to `0`/anonymous if not supplied — the server
  doesn't crash, but permission-scoped tools (`manage_servers`,
  `get_user_context`) return empty results for an unrecognized user, and
  `send_notification` hard-fails without a chat ID. An external harness
  would need *some* identity wiring to get full value, though this is a
  much smaller lift than replicating Condor's own runtime.

**The real boundary, then, is not "MCP server vs. everything else Condor
does" — it's "the chat/session frontend (ACP subprocess spawning, Telegram
polling, web dashboard chat UI) vs. the backend (web API + TickEngine host +
strategy/journal system)."** The frontend is what's genuinely redundant with
what Claude Code, Cursor, or any other harness already provides. The
backend is not redundant with anything a chat harness ships — it's real,
Condor-specific infrastructure that has to keep running regardless of which
harness a human types into.

## 3. What this means for the "focus on the MCP layer" idea

The idea is directionally right, and cheap to act on: **publish a short,
harness-agnostic integration guide** — the exact `.mcp.json`-equivalent
config block (`{name, command, args, env}`, already produced internally by
`build_mcp_servers_for_session`/`build_mcp_servers_for_agent` in
`handlers/agents/_shared.py`) plus a note on what identity/backend
dependencies apply per tool — so anyone using Claude Code, Cursor, Hermes-
agent, or OpenClaw can point at Condor's tools directly. This is close to
zero-cost: the config shape already exists internally, it just isn't
documented externally yet (confirmed: no README/doc currently describes
this).

This sharpens [`fork-vs-build.md`](./fork-vs-build.md)'s conclusion rather
than replacing it. That doc already argued against building or forking a
bespoke chat runtime; this finding pushes the same logic one step further —
not just "don't fork someone else's chat runtime," but "don't over-invest in
maintaining Condor's *own* bespoke chat runtime either, once any harness can
already reach the tools that matter." What remains Condor's job regardless:
keeping the backend/TickEngine/journal system running and well-documented,
and building the skill/routine/strategy content that gives any harness
something worth calling.

This also reframes §5's hosted-instance product, for the better: the pitch
isn't "use our chat UI," it's **"you don't run a chat frontend yourself —
we run the always-on backend/execution engine, plus a harness of your
choice, on your own box, and you reach it from your phone."** That's a
stronger, more differentiated position than competing on chat UX. One
mechanism correction, worked out in `roadmap-v2.md` Phase 2: for the
*hosted* tier specifically, "bring your own harness" doesn't mean the
customer's own local Claude Code reaching into a remote server — the
harness (Condor's own runtime, OpenClaw, or Hermes) is co-located on the
hosted box itself, chosen at provisioning time, and **Telegram** (via
whichever harness's own Telegram integration) is the customer's actual
access point — no local tooling required at all. The "point your own local
harness at a stdio server" framing still applies fully to self-hosted
installs; it just isn't how the hosted product's access mechanism works.

## 4. So what's actually defensible? (Assuming harness *and* execution layer are both swappable)

Taking the premise fully seriously — any chat harness works, and the
execution layer itself could be Hummingbot today, CCXT tomorrow, or a
next-generation Hummingbot later — forces a clean separation between what's
commodity and what isn't.

**Not defensible, and this question's premise makes that more obvious, not
less:**

- **The tool-wrapping glue itself** — the MCP tool schemas that call into
  whatever execution backend is configured. If the execution layer is
  explicitly swappable, then by construction this layer is a thin adapter,
  not IP — structurally identical to what Hummingbot's own connector
  abstraction already treats as commodity (many exchanges, one interface).
  Assume this gets reimplemented by anyone with a weekend and an execution
  API to point at; business-strategy.md §10 already established the same
  conclusion about code generally ("the moat was never the code"), and this
  scenario is the sharpest possible instance of it.
- **The chat/session frontend** — per §2/§3 above, this is now confirmed
  redundant with what external harnesses already provide, not just
  theoretically replaceable.

**Actually defensible:**

1. **The TickEngine/strategy runtime infrastructure itself** — a persistent,
   periodic tick-loop execution engine with structured journaling
   (`sessions/session_N/journal.md`), curated cross-session learnings
   (`learnings.md`), full per-tick snapshots, dry-run/backtest tracking, and
   a deterministic shutdown-policy sequence (architecture doc §4-5). No
   chat harness ships this — Claude Code, Cursor, Hermes-agent, and OpenClaw
   are all built around *interactive or on-demand* agent sessions, not
   unattended, self-documenting, always-on strategy execution with an audit
   trail. This is a different product category from "chat with tools,"
   and it's where Condor's actual hard engineering has gone.
2. **Accumulated per-strategy operational track record** — `learnings.md`
   and session journals that compound with real running time. A fork or a
   from-scratch reimplementation starts with an empty history; a strategy
   that's been running against real capital for months has a track record
   nothing can replicate on day one. This is the same logic as
   business-strategy.md §10's "captured capital/liquidity" moat, applied to
   earned *operational* history rather than capital — and it survives even
   if the execution backend underneath is later swapped.
3. **Real trading/quant domain expertise encoded in skills and routines** —
   distinct from the generic tool-wrapping glue in point 1 above. A skill
   like `pmm_mister_deploy` or a routine like `oracle_jit_backtest.py`
   encodes actual operating knowledge from running real mandates (Ripple,
   MetaDAO, USDM1 via Botcamp Solutions) — this is the "alpha," not the
   "infra." It's copyable once published open source (per §10's standing
   assumption), but genuinely hard to *originate* without the same
   operating experience, which is a real, if softer, form of durability.
4. **Brand, community, and distribution** — unchanged from business-
   strategy.md §10: Hummingbot's existing multi-year community and
   Botcamp Solutions' actual paying client relationships don't transfer to
   a fork or a competing harness integration, regardless of tool or
   execution-layer choice.
5. **Hosted/managed execution** (§5, refined in §12) — reliability and
   ops convenience for the backend/TickEngine layer specifically, now more
   clearly separable from "which chat interface the customer prefers,"
   per §3 above.
6. **The tokenized-strategy / Swig-secured-capital layer**, if pursued
   (business-strategy.md §9/§11b) — a categorically different kind of
   moat (captured capital and liquidity network effects), entirely
   orthogonal to both the harness-choice and execution-layer-choice
   questions addressed here.

## 5. Real-world validation: a live competitor already ships exactly this split

[Tread Labs](https://github.com/tread-labs-public/taas_mcp) (`tread.fi`, an
~8-9-person, $3.5M-pre-seed NYC/Bangkok crypto trading-infra startup founded
by ex-Morgan Stanley quant David Jeong) publishes `taas_mcp` — a public,
MIT-licensed MCP client for their proprietary trading/OEMS backend — and its
design independently confirms this doc's thesis rather than complicating
it.

- **It's the thinnest possible version of "MCP tool layer, bring your own
  harness."** The client hardcodes *zero* tools — on startup it fetches the
  tool catalog live from `GET /api/mcp/tools` on Tread's closed backend and
  re-exposes whatever's returned as MCP `Tool` objects. The README's own
  framing: *"All reasoning happens in your local agent; Tread executes the
  individual tool calls."* Tools cover market data, balances/portfolio,
  order status, and order placement (single/market-maker/delta-
  neutral/batch) with cancel/pause/resume/leverage controls — a real,
  production order surface, not a toy.
- **It explicitly disclaims the layer this doc argues is the actual
  moat.** Verbatim from the repo: *"The local agent owns risk. Tread runs
  no server-side agent for these calls — there is no automated margin
  management, invariant enforcement, or liveness rescue behind them."*
  There is no scheduler, no tick loop, no journaling/audit trail, no
  skill/routine/domain-agent ontology anywhere in the client — confirmed
  directly, not inferred. A live, funded competitor building in exactly
  this space looked at the same "MCP tools + any harness" shape and
  deliberately stopped there, leaving unattended execution and accumulated
  operating knowledge as someone else's problem — which is precisely §4's
  claim about where the real differentiation has to live.
- **Two technical patterns worth adopting regardless of the competitive
  read:**
  1. **A live, backend-driven tool catalog with no client-side tool
     definitions.** Because the client fetches its tool list from the
     backend on every session rather than hardcoding schemas, Tread can
     add/change/remove tools purely server-side — no client release, no
     version skew. Condor's MCP tool surface is currently defined and
     versioned in the server code itself (`mcp_servers/condor/server.py`,
     `mcp_servers/hummingbot_api`); a live-catalog pattern is a genuinely
     reusable idea if Condor ever wants to iterate on tool schemas faster
     than deployed clients update.
  2. **A single, server-generated "operating guide" prompt instead of a
     static doc, specifically to prevent drift.** Tread's `SKILL.md`
     deliberately does *not* duplicate its own operating instructions in
     the repo — it points at a live MCP `prompt` (`tread_operating_guide`)
     generated from the same source of truth as the tool catalog, with the
     explicit rationale "so the two never drift." Condor's skill/routine
     markdown (architecture doc §2-3) is hand-maintained prose that can
     drift from what the underlying tools/routines actually do — this
     pattern (generate the guide, don't hand-write it, from the same
     source as the schema) is worth considering for Condor's own
     skill-authoring workflow.
  3. **A narrow but real correctness gotcha, worth checking Condor's own
     MCP tools against:** Tread's backend returns tool-level failures as
     HTTP 200 with an in-band `{"ok": false, "error": ...}` body, which
     their HTTP client wouldn't raise on by default — they shipped a
     dedicated fix (commit "CR-003") to surface that as an MCP `isError`,
     so a failed order doesn't silently look like a success to the calling
     LLM. Worth auditing `mcp_servers/condor/tools/*.py` and
     `mcp_servers/hummingbot_api` for the same failure mode — any tool that
     returns a dict with an internal error/success field rather than
     raising is exposed to this exact bug.

## 6. Implication for the open "Keep or kill Assistants?" roadmap decision

This sharpens [`roadmap-v2.md`](./roadmap-v2.md) Phase 1's "Keep or kill
Assistants?" item — deliberately left open there — into a more radical
version of the same fork, worth folding into that same decision rather than
resolving separately here:

Not just "collapse the assistant/agent schema split," but **"should Condor
keep building/maintaining its own bespoke chat-session frontend at all, or
publish harness-agnostic integration instructions and put all remaining
frontend-adjacent engineering effort into the backend/TickEngine/content
layer instead?"** This doesn't have to be all-or-nothing — Condor's own
Telegram bot remains genuinely good UX for the prosumer audience (§3b) and
can stay as one first-class option — but it argues for treating it as one
integration surface among several from here on, not the center of gravity,
and for prioritizing engineering time accordingly: backend/TickEngine
robustness and skill/routine/strategy content quality over chat-frontend
polish.

## 7. Recommendation

1. **Publish a harness-agnostic MCP integration guide now** — low cost, the
   config shape already exists internally
   (`build_mcp_servers_for_session`/`build_mcp_servers_for_agent`), it's
   just not documented externally. Explicitly document the §2 boundary
   (which tools are self-contained vs. require the backend running) so
   users aren't surprised by `APIError`s from tools that need it.
2. **Don't treat this as a reason to build a fuller standalone MCP-only
   product.** The backend/TickEngine dependency for `consult`/`delegate`/
   strategy management is real and by design — publishing instructions
   doesn't eliminate the need to keep that backend running and well
   documented; if anything it raises the bar on making that backend easy
   to stand up (self-hosted) or trivially available (hosted, §5/§12).
3. **Redirect engineering priority accordingly**: continue investing in the
   TickEngine/journal/learnings system and the skill/routine/strategy
   content library (§4, points 1-3) as Condor's real product; treat the
   chat/session frontend as a solved-elsewhere problem for anyone who'd
   rather use their own harness, while keeping Condor's own Telegram/web
   chat as one good option, not the flagship investment.
4. **Feed this directly into the open Phase 1 decision** (§6 above) rather
   than resolving it here — it changes the shape of that question, not the
   fact that it's still a real decision for the team to make deliberately.
5. **Adopt the live-tool-catalog and generated-operating-guide patterns
   (§5)** where they fit Condor's own MCP servers, and audit Condor's tool
   implementations for the in-band-error/`isError` gotcha Tread had to fix.
