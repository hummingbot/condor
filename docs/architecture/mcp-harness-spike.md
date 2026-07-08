# Spike: validating Condor strategies via MCP from Claude Code / OpenClaw / Hermes

Companion to [`mcp-first-value-add.md`](../strategy/mcp-first-value-add.md) (the
harness-agnostic thesis this spike tests empirically) and
[`strategy-engine-and-shared-intelligence.md`](./strategy-engine-and-shared-intelligence.md)
(§1.2's request-path walkthrough, which this spike is designed to confirm against
real harnesses instead of just the code). **Scope note**: pluggable *scheduling*
(OpenClaw/Hermes cron firing ticks, §1.5–1.6 of that doc) is deferred and out of
scope here — this spike keeps Condor's own `TickEngine` as the sole scheduler
throughout. It tests something narrower and already in-scope today: can
OpenClaw, Hermes, and Claude Code be used as **Tier 1 interactive MCP clients** —
starting, consulting, and stopping a strategy — in place of Condor's own
Telegram bot or web dashboard.

## Goal

Decide whether Telegram and the web dashboard can be genuinely deprioritized as
Condor's interaction surface, by concretely validating: start the
`market_making_expert` agent's `pmm_mister_operator` strategy from Claude Code,
OpenClaw, or Hermes, and confirm it runs identically to starting it from
Condor's own Telegram bot — same tools, same running `TickEngine`, same
journal, visible and controllable from any of these interchangeably.

## The key finding, grounded in the code: this is a narrower test than it sounds

Reading `condor/agents/engine.py`'s actual `_create_client()`
(`engine.py:552-620`) settles the central question before any spike is run:
**a running strategy's tick execution is already harness-invariant by
construction.** Walk the call chain:

1. Any harness calls `mcp__condor__manage_trading_agent(action="start_agent",
   strategy_id="market_making_expert.pmm_mister_operator", config={...})`
   (`mcp_servers/condor/tools/trading_agent.py:337-385`).
2. That handler does nothing agent-specific — it resolves the strategy, then
   `POST`s to Tier 2's own REST API,
   `/agents/{agent_slug}/strategies/{sslug}/start`, passing
   `settings.chat_id`/`settings.user_id` (identity carried by the *calling*
   MCP subprocess, not by the strategy).
3. Tier 2 creates a `TickEngine` inside its own persistent process. From this
   point on, `_loop()`/`_tick()`/`_create_client()` run **entirely inside
   Tier 2**, on Tier 2's own schedule, spawning Tier 2's own ACP/PydanticAI
   client each tick (`engine.py:552-620`) — calling
   `build_mcp_servers_for_agent`/`build_mcp_servers_for_session`
   (`handlers/agents/_shared.py:305-440`) itself, and resolving
   `agent_key: claude-acp:sonnet` to the `claude-agent-acp` bridge command via
   `resolve_acp()` (`condor/acp/client.py:168-206`) itself.

None of step 3 reads anything about which harness issued the `start_agent`
call in step 1. Once started, the strategy's actual market-making behavior —
which model reasons about it, which tools it calls, how it's journaled — is
**already guaranteed identical** regardless of whether a human typed
`start_agent` from Telegram, Claude Code, OpenClaw, or Hermes. That part of
"does it work the same" doesn't need a spike; it's a fact about the code.

**What the spike actually needs to validate is narrower and lives entirely at
the Tier 1 boundary**, none of which is exercised by today's Telegram/web-only
usage:

1. Can each harness be configured with a Condor MCP server pointed at valid
   identity (chat_id/user_id/server_name), the same way
   `build_mcp_servers_for_agent` configures it today for Condor's own
   sessions?
2. Does each harness successfully call `start_agent`, `consult`, `delegate`,
   `trading_agent_journal_read`, `stop_agent` end-to-end — no harness-specific
   quirk (tool-call formatting, auto-approval/permission prompts blocking an
   unattended call, timeout handling) breaks the flow?
3. Does the resulting running strategy show up identically in Condor's *own*
   tooling — `list_agents`, the web dashboard, a Telegram `/status` — proving
   Tier 2 state was never tied to the harness that started it (the concrete,
   observable version of architecture doc §1.2's claim)?

## Does `market_making_expert` / `pmm_mister_operator` need modification?

**Agent-level (`AGENT.md`): no.** `agent_key: claude-acp:sonnet`, the `tools:`
allowlist, and `server_name: moneymaker` are all resolved by Tier 2 code paths
(`_create_client`, `_resolve_server`) the same way regardless of which harness
is consulting/delegating to it or which harness started its strategy. Nothing
here is Telegram- or dashboard-specific.

**Strategy-level: yes, one pre-existing bug should be fixed first, unrelated
to the harness question.** `pmm_mister_operator/strategy.md`'s Step 1 calls
three routines every tick: `market_analyzer`, `portfolio_scanner`, and
`bot_position_tracker`. Checking the actual routines directory
(`agents/market_making_expert/routines/`) and a repo-wide search for those
filenames turns up only `market_analyzer.py` and `mm_dashboard.py` —
`mm_dashboard.py`'s own docstring says it **"Consolidates three former
routines... portfolio_scanner ... mm_bot_report ... bot_position_tracker
(superseded)"**. `portfolio_scanner` and `bot_position_tracker` no longer
exist as routines anywhere in the repo (routine names resolve from filename
stems, per `discover_routines_from_path`, `routines/base.py:242-280`) — so
every tick today calls two routine names that resolve to
`{"error": "Routine '...' not found"}`, **regardless of scheduler or
harness**. This isn't a harness-compatibility issue; it's a stale reference
left over from the `mm_dashboard` consolidation. Fix before running the
spike, or the spike will conflate "the harness doesn't work" with "the
strategy was already broken":

```diff
- `market_analyzer` with `{"trading_pair": "<trading_pair>", "connector_name": "<connector_name>"}`
- `portfolio_scanner` with `{"connector_name": "<connector_name>"}`
- `bot_position_tracker` with `{"trading_pair": "<trading_pair>"}`
+ `market_analyzer` with `{"trading_pair": "<trading_pair>", "connector_name": "<connector_name>"}`
+ `mm_dashboard` with `{"connector_name": "<connector_name>", "trading_pair": "<trading_pair>"}`
```

(`mm_dashboard.Config` already accepts both `connector_name` and
`trading_pair` — a strict superset of what the two retired routines took —
so this is a one-line Step-1 edit, not a rewrite.)

**One real, named non-parity gap (not a blocker, but sharper than first
described once a TUI is in the picture): `send_notification`
(`mcp_servers/condor/tools/notification.py`) is Telegram-only, hardcoded.**
It POSTs directly to `api.telegram.org` using `settings.bot_token`/
`settings.chat_id` — there is no branch for "notify via whatever harness is
active." Originally this was framed as "fine as long as Telegram stays the
alerting channel, only a gap if Telegram is later dropped entirely" — but
that framing assumed whatever replaces Telegram is itself push-capable.
**If the replacement interactive surface is a TUI — OpenClaw's TUI or Claude
Code's terminal UI — that assumption doesn't hold, and not as an
implementation gap but as a structural property of TUIs:**

- Telegram (and any messaging-channel adapter — Discord, Slack, SMS) is
  **persistent and push-capable**: a message can reach the user's phone at
  3am with nothing already open on their end. A `TickEngine` tick alerting
  on a stop-loss trigger needs exactly this, because most of the time
  nobody has anything open — that's the entire point of an unattended,
  24/7 strategy loop.
- A TUI is **session-scoped and pull-based**: it only exists while a human
  has a terminal window open, the same limitation already named for Claude
  Code's `/loop` (session-scoped, dies on terminal exit) in the scheduling
  research (`strategy-engine-and-shared-intelligence.md` §1.5). There is
  no mechanism by which a background tick can make a TUI that isn't running
  suddenly appear on someone's screen. A TUI is structurally never a valid
  `send_notification` target, no matter how much engineering goes into it —
  this isn't "not implemented yet," it's "there's nothing to dispatch to."

**Decision: stick with Telegram as the alert channel for now** — nothing
here needs to change today, and Telegram alone still carries every alert
unmodified regardless of which harness starts or consults a strategy. The
point worth carrying forward isn't "switch off Telegram," it's narrower and
more specific than "make `send_notification` multi-channel across
harnesses": **separate *interactive harness* from *alert channel* as two
independent choices, not one**, and worth genericizing precisely *because*
OpenClaw and Hermes already ship their own messaging adapters — the
capability to dispatch elsewhere already exists on their side, it's
Condor's own `send_notification` that's hardcoded to one channel. Route it
to whichever **persistent, push-capable channel** is configured — which may
or may not be the same thing as the interactive harness a user is currently
typing into:
- OpenClaw's TUI + OpenClaw's own Telegram/Discord/Slack adapter (OpenClaw
  ships both, per the scheduling research) — TUI for hands-on sessions,
  channel adapter for alerts, both under one harness.
- Claude Code's terminal UI has no push-capable channel of its own at all —
  if Claude Code's terminal is the interactive surface, *something else*
  (Telegram, email, SMS) still has to carry alerts; there is no in-harness
  substitute to fall back to.
- Hermes-agent: same shape as OpenClaw — a cron/gateway process plus its own
  messaging adapters, separate from however a human is interacting with it
  at a given moment.

This also reinforces, rather than undermines, `roadmap-v2.md` Phase 2's
existing decision that Telegram is the hosted customer's access point: even
in a world where the *interactive* choice moves to a TUI-based harness for
power users, the *alerting* requirement doesn't move with it — some
persistent channel is still needed underneath, for exactly the reason a
hosted, unattended strategy can't rely on someone having a terminal open.
**Not needed for this spike** — tracked as a `## Follow-ups` item instead.

**Routines are not a general blocker, already solved.** `mm_dashboard.py`
imports `telegram.ext.ContextTypes` only as a type hint for its `run(config,
context)` signature — `manage_routines`'s `run_routine` handler already
constructs a `MCPContext` mock (`mcp_servers/condor/tools/routines.py:146-`
`162`) instead of a real python-telegram-bot `Context`, using an HTTP-fallback
bot for any message delivery. This already works identically from any MCP
caller today — nothing to fix. (Continuous routines *can't* run via MCP at
all — an explicit `{"error": "... use the Telegram /routines command"}` —
but `pmm_mister_operator` uses none, so this doesn't affect the spike; noted
as a known limitation, not something this spike needs to touch.)

## Setup prerequisites

- **Identity — reuse an existing authorized user, don't invent one.**
  `config_manager`'s permission model (`get_server_permission`/
  `get_accessible_servers`, `config_manager.py:699-732, 802`) is keyed by
  `user_id`; an arbitrary new id has no `server_access` entry and
  `start_agent`'s fallback (`trading_agent.py:367-370`) would resolve to *no*
  accessible server, silently starting the strategy with no exchange
  connectivity. Use the same identity already on these files (`created_by:
  481175164` in both `AGENT.md` and `strategy.md`) as the `--chat-id`/
  `--user-id` passed to the MCP subprocess.
- **Server — point at paper/testnet, not `moneymaker` (production).**
  `pmm_mister_operator`'s default `server_name` resolves to whatever
  `--server-name` is passed at MCP-server-spawn time (mirroring
  `build_mcp_servers_for_agent`, `handlers/agents/_shared.py:384-440`, which
  resolves a server's host/port/username/password via
  `config_manager.get_server(name)`). For a first spike, use a paper-trading
  or testnet server config, not the live-capital one — an explicit safety
  gate, not implied by anything in the code itself.
- **Per-harness MCP registration** — all three need the same two stdio MCP
  servers Condor's own sessions already use
  (`handlers/agents/_shared.py:305-440`), just registered through each
  harness's own config mechanism instead of Condor's dynamic
  `build_mcp_servers_for_*`:
  ```
  condor:          uv run python -m mcp_servers.condor \
                     --chat-id <id> --user-id <id> --server-name <paper-server>
  mcp-hummingbot:  uv run python -m mcp_servers.hummingbot_api \
                     --url <paper-server-url> --username <u> --password <p> \
                     --server-name <paper-server>
  ```
  - **Claude Code**: add both as `mcpServers` entries (project `.mcp.json` or
    `claude mcp add`), with `args` including the flags above — same shape as
    this repo's own `.mcp.json`, just with explicit identity/server flags
    instead of relying on Condor's session code to inject them via env.
  - **OpenClaw / Hermes**: same two stdio server definitions, registered
    through each project's own MCP config surface. Exact config syntax for
    each is an **open item to verify against their docs at implementation
    time** — flagged here rather than assumed, consistent with how the
    scheduling spike (`strategy-engine-and-shared-intelligence.md` §1.6,
    Hermes' cron registration) already treats its own unverified integration
    points.

## Test protocol

1. **Fix the stale routine references** in `pmm_mister_operator/strategy.md`
   (above) before anything else.
2. **Register both MCP servers** in one harness (start with Claude Code, the
   easiest to configure) per Setup, pointed at the paper/testnet server.
3. **Start the strategy in `dry_run` or paper mode**, not `loop` against real
   capital:
   ```
   manage_trading_agent(action="start_agent",
     strategy_id="market_making_expert.pmm_mister_operator",
     config={"trading_pair": "SOL-USDT", "connector_name": "<paper-connector>",
             "execution_mode": "dry_run"})
   ```
4. **Verify Tier 2 statehood, the single most important assertion**: close
   the harness's session/terminal entirely, then reopen it (or open a
   *second*, independent harness — see step 6) and call `list_agents` /
   `trading_agent_journal_read` — confirm the strategy is still ticking and
   its journal has advanced. This is the concrete, observable proof of
   architecture doc §1.2's claim: Tier 2 state was never tied to the harness
   that started it.
5. **Consult and delegate**: from the same harness, `consult(agent=
   "market_making_expert", task="what's the current regime and inventory
   status?")` — verify the answer matches what Condor's own Telegram bot
   would produce for the identical question (same agent definition, same
   tools, same `tool_preload_hint` behavior for ACP-based harnesses per
   `build_initial_context`, `handlers/agents/_shared.py:487-519`).
6. **Cross-harness check**: register the same two MCP servers in a *second*
   harness (OpenClaw or Hermes), and from there call `list_agents`/`consult`/
   `stop_agent`/`pause_agent` against the strategy **started in step 3 from
   Claude Code**. This is the test that most directly answers the spike's
   stated goal — if a strategy started from one harness is fully visible and
   controllable from a completely different one, the three-tier
   architecture's core claim is validated end-to-end, not just in code.
7. **Stop cleanly**: `stop_agent`/`shutdown_agent` from whichever harness is
   convenient — verify it matches what the web dashboard's stop button or a
   Telegram `/stop` does today (same MCP tool, same Tier 2 endpoint
   underneath — nothing harness-specific to check here beyond "it returns
   the same way").
8. **Only after steps 3–7 pass cleanly on paper/dry-run**, consider a short,
   minimal-size live-capital run as an explicit go/no-go decision — not
   implied by a clean paper-mode pass. Treat this as a separate approval
   gate, given real capital is involved.

## Success criteria

- Steps 3–7 all pass with no harness-specific failure (auth, tool-call
  formatting, timeout, or permission-prompt issues).
- Step 6 (cross-harness visibility/control) is the load-bearing result: if
  it passes, Telegram and the web dashboard are demonstrably no longer
  required for either starting or operating a strategy — only for the
  interactive-chat and alerting conveniences named in the non-parity gap
  above.
- If step 6 fails for a reason **not** explained by this doc's identified
  gaps (stale routines, Telegram-only notifications), that's a real,
  previously-unknown parity gap — write it up as a new, concretely-described
  finding rather than working around it silently, since the point of this
  spike is to find these before they're discovered by a real hosted
  customer.

## Follow-ups (not blockers for this spike)

- Genericize `send_notification` to dispatch via whichever **persistent,
  push-capable channel** is configured, not hardcoded to Telegram's Bot
  API — worth doing *because* OpenClaw and Hermes already ship their own
  Telegram/Discord/Slack adapters (the capability already exists on their
  side; Condor's own tool is the thing hardcoded to one channel), not
  because Telegram itself needs to go away. Telegram stays the channel for
  now either way. Explicitly **not** the interactive TUI itself as a
  target, since a TUI has no mechanism to receive a push when nothing is
  open — if the interactive harness is Claude Code's terminal specifically,
  there's no in-harness channel to fall back to at all, so some persistent
  channel (Telegram or otherwise) is still required regardless of harness
  choice.
- If step 6 passes, this spike is the concrete evidence
  `mcp-first-value-add.md`'s harness-agnostic pitch needs — worth a pointer
  back from that doc once results are in.

## Results (executed 2026-07-07)

**Safety gate found and fixed before running anything.** Checking this
environment before starting the strategy: it has exactly one configured
Hummingbot server (`local`, `config.yml`), and `get_portfolio_overview`
(read-only) showed it holds **real capital** — ~$1019 across Hyperliquid and
Backpack, with real open orders. Re-checking `execution_mode="dry_run"`'s
actual enforcement (`condor/agents/risk.py`, `handlers/agents/_shared.py`)
against that fact surfaced a real gap: dry-run blocked `manage_executors`/
`place_order`/gateway actions, but not `manage_bots(deploy)` —
`pmm_mister_operator`'s actual deployment path — because `manage_bots` wasn't
in `DANGEROUS_TOOLS` at all. Fixed (`DANGEROUS_BOT_ACTIONS` added, dry-run
block extended, `BASE_PROMPT_DRY_RUN` updated for defense in depth,
4 new tests added, all 197 repo tests pass) and filed as
[hummingbot/condor#151](https://github.com/hummingbot/condor/issues/151)
before proceeding — this was a prerequisite the spike surfaced, not
something the spike was looking for.

**Claude Code: fully executed.** A headless `claude -p` session, given a
scratch `--mcp-config`/`--strict-mcp-config` (kept separate from this repo's
own `.mcp.json` so the live production bot's config wasn't touched) with
explicit `--chat-id`/`--user-id 456181693`/`--server-name local`, successfully:
called `list_agents` against the **live, already-running** Tier 2 backend
(the same process backing the user's real Telegram bot); started
`market_making_expert.pmm_mister_operator` with `execution_mode="dry_run"`;
observed it as `status: "running"` via `list_agents`; and produced a full
tick transcript (`dry_runs/experiment_1.md`) confirming the routine fix from
issue #150 actually works end-to-end (`"Running market_analyzer and
mm_dashboard routines in parallel"`) and that the model behaved correctly —
narrating "🧪 Would deploy bot `sol-mm`..." conditionally rather than
attempting a real deploy, consistent with (though, since it never attempted
the call, not itself proof of) the new dry-run gate above.

**Correction to this doc's own test protocol, discovered empirically**:
`dry_run`/`run_once` are **one-shot experiment modes**, not persistent loops
— confirmed by the strategy disappearing from `list_agents` entirely after
its single tick completed (`engine.py`'s `is_experiment` framing implies
this, but the spike's step 4, "close the harness, confirm it's still
ticking," implicitly assumed a persistent strategy). That means step 4's
actual cross-process-statehood assertion needs `execution_mode="loop"` (or
a real non-experiment strategy), not `dry_run` — which reintroduces the real-
capital question above and needs its own explicit go/no-go, not a rerun of
this same dry-run test.

**OpenClaw: fully executed (second pass, same day).** The first attempt was
blocked by two local-install issues, both fixed in place: the gateway
LaunchAgent was installed by an older OpenClaw (2026.3.13 plist vs 2026.6.11
CLI) and not loaded — `openclaw doctor --repair` bootstrapped it and
`openclaw gateway install` rewrote the stale plist (status now clean:
running, probe ok, versions matched); and the earlier
`ProviderAuthError: No API key found for provider "anthropic"` disappeared
after the doctor repair migrated the auth profiles into the `main` agent's
store (`openclaw models status` now shows `anthropic:default static` +
`google:default static`). The Anthropic profile then failed on **billing**
(claude.ai "out of extra usage"), so the live turns ran on
`--model google/gemini-2.5-pro` — incidentally a nice extra data point:
the Tier 1 harness *and* its model are both swappable without Condor
noticing. With that, `openclaw agent --local` turns completed the full
protocol: `list_agents` returned the identical response Claude Code got;
`start_agent` launched `market_making_expert.pmm_mister_operator_e2` in
dry_run (observed `status: "running"` in the same turn); and the tick
completed and wrote `dry_runs/experiment_2.md`, ending with "No executors
were created (dry run)".

**Cross-harness handoff (step 6): executed and passed.** An agent started
from headless Claude Code (`..._e4`, session 4) was stopped from OpenClaw
mid-tick (`stop_agent` → `{"stopped": true}`, then `list_agents` → empty).
Session numbers interleaved across harnesses within the same backend
(1 = Claude Code, 2 = OpenClaw, 3 = Claude Code, 4 = Claude Code
started / OpenClaw stopped) — shared Tier 2 state across harnesses is
demonstrated, not just argued.

**Architectural correction discovered while verifying who ran the tick**:
`start_agent`/`stop_agent`/`list_agents` in the Condor MCP server do NOT
host the TickEngine in the per-harness MCP subprocess — they delegate to
the persistent main process over its web API
(`mcp_servers/condor/tools/trading_agent.py`'s `_agent_lifecycle` →
`call_main_api("POST", "/agents/.../start")`). The engine registry lives in
`main.py`'s process. This is *stronger* than the doc's original
harness-invariance argument (ticks are identical across harnesses because
they all run in the same process, full stop), and it's why the OpenClaw CLI
exiting mid-tick didn't matter. Two practical consequences: (a) an external
harness needs the Condor main process up — MCP tools alone don't carry
Tier 2; (b) **code fixes don't reach Tier 2 until the main process
restarts** — see follow-ups.

**Consult path (running the market_making_expert as a domain agent):
executed from OpenClaw and equivalent by construction.** `consult` is the
same shape as `start_agent`: the MCP tool just forwards to the main process
(`consult.py` → `call_main_api("POST", "/agents/{agent}/consult")`), so the
worker runs in `main.py` with the agent's own configured model
(`claude-acp:sonnet` from AGENT.md), memory, and skills — the calling
harness's model (gemini in this test) never leaks into the domain agent. A
live `consult(agent="market_making_expert", ...)` from OpenClaw returned a
full regime read (ranging, moderate-quieting vol, symmetric 0.05%/0.10%
spreads, TP floor above round-trip fees) and correctly honored advisory
mode ("No deployment action warranted") per AGENT.md's consulted-mode
contract. One integration finding: **OpenClaw's default per-request MCP
timeout (~60s) is too short for consult/delegate-class tools** (a consult
runs a full worker session, 1–3 min; the tool's own budget is 180s) — the
first attempt died with `MCP error -32001: Request timed out`. Fixed by
registering with `openclaw mcp add condor ... --timeout 240`. This belongs
in the harness integration guide: long-running Condor tools need the
client-side MCP timeout raised above 180s.

**Creating agents from OpenClaw (the full agent_builder loop): executed
and passed.** Beyond running pre-existing agents, the spike's key question —
can a user *create and run* trading agents from OpenClaw — was validated
end-to-end with a brand-new agent, entirely via `openclaw agent` turns on
gemini-2.5-pro:

1. `create_agent` → `funding_rate_watcher` written to
   `agents/funding_rate_watcher/AGENT.md` with correct frontmatter and
   `created_by: 456181693` (the MCP identity carried through creation).
2. `consult(agent="funding_rate_watcher", ...)` → alive on first try:
   fetched real Hyperliquid funding data and returned a correct
   domain assessment per its instructions (the agent_builder skill's
   "prove it's alive" step).
3. `create_strategy` → `funding_rate_watcher.funding_snapshot`, then
   `start_agent` with `execution_mode="dry_run"` → observed
   `status: "running"` with `server_name: "local"`, tick completed,
   `dry_runs/experiment_1.md` written.
4. **Routine authoring via the routine_builder agent works from OpenClaw
   too**: `consult(agent="routine_builder", task="Create a routine named
   funding_check ...")` produced
   `agents/funding_rate_watcher/routines/funding_check.py` — the builder
   read existing routines for patterns, wrote the file, and *tested it
   itself* before answering. A follow-up
   `manage_routines(action="run", name="funding_check", ...)` from OpenClaw
   executed it live (rate −0.0007%/8h, NEUTRAL). The routing rule's
   "routine authoring goes through routine_builder" contract holds across
   harnesses because consult itself does.

One model-behavior note, not a Condor issue: in a multi-step turn, gemini
misread the one-shot dry_run agent's expected self-stop ("agent gone from
list_agents") as instability and abandoned its remaining steps — worth
remembering that experiment modes' disappear-after-one-tick behavior can
confuse a harness model that was told to operate on the agent afterward.
Single precise-instruction turns had no such problem.

**Second real pre-existing bug found by the spike (harness-independent):
serverless agent consults/ticks lose their memory/skill scope
([hummingbot/condor#152](https://github.com/hummingbot/condor/issues/152)).** The
routine_builder consult above answered "The cookbook skill isn't available"
— yet `agents/routine_builder/skills/routine_cookbook/` exists and
`SkillStore('routine_builder')` reads it fine. Root cause:
`consult.py` passes `agent_slug` to the MCP config only on the
served branch; the serverless branch (`server_required: false`, which is
exactly routine_builder) called `build_mcp_servers_for_session()`, which
had no `agent_slug` parameter at all — so the worker's condor MCP
subprocess ran with empty `settings.agent_slug` and `manage_skill`/
`manage_memory` silently resolved to the **chat condor's stores**: the
agent can't see its own skills, and anything it writes pollutes the chat's
memory. Same gap in `TickEngine._create_client()` for strategies without
`server_name`. Fixed: `build_mcp_servers_for_session()` gained an
`agent_slug` param, both serverless branches now pass the slug, and
`test_session_mcp_servers_carry_agent_slug` locks it in (also asserting
chat sessions still get no `--agent-slug`). 201 tests pass. Like the
routine-name bug (#150), this predates the spike and bites every harness
including Telegram — the spike just made it visible because the
routine_builder consult's answer narrated the failed skill read. Verified
live after a main-process restart: the identical consult that errored with
"Skill 'routine_cookbook' not found" now reads the skill successfully.

**Hermes: correctly out of scope** — not installed, not attempted, per
scoping direction partway through this spike.

**Net**: the core claim — Tier 1 harness swap works, Tier 2 execution is
unaffected — is now validated **end-to-end for both Claude Code and
OpenClaw**, including the cross-harness handoff, on two different frontier
models. Nothing Condor-side blocked any step. The remaining open item is
the `loop`-mode persistence test (below), which is a real-capital go/no-go
question, not a harness question.

## Follow-ups discovered during execution, not planned in advance

- **hummingbot/condor#151** (dry-run gap) — fixed and merged into this
  branch's working tree during this spike; see above.
- **~~RESTART THE MAIN CONDOR PROCESS to activate the #151 fix~~ — DONE
  (same day).** The `main.py` running since Jul 2 predated the fix and held
  the old `risk.py`/`prompts.py` in memory — visible in
  `experiment_2.md`/`experiment_3.md`, whose system prompts show the
  pre-fix `BASE_PROMPT_DRY_RUN` text (both ticks verified clean — no
  mutating calls attempted). The user restarted it; a verification dry-run
  tick (`experiment_4.md`) confirms the new prompt text (and therefore the
  new code) is live in the backend. Lesson kept for ops: a code fix on disk
  does not reach Tier 2 until the main process restarts.
- **The OpenClaw `main` agent's Anthropic auth profile is present but out of
  claude.ai usage quota** (billing, not a credential gap — the earlier
  `ProviderAuthError` was resolved by `openclaw doctor --repair`). Turns
  work on the Google profile; top up or switch the default model if
  Anthropic-backed OpenClaw turns are wanted. Not a Condor-side task.
- **A `loop`-mode (or real non-experiment) test still needs to happen** to
  validate persistent cross-process statehood (this doc's original step 4)
  — `dry_run`/`run_once` being one-shot means they can't exercise that
  assertion. Needs its own real-capital-safe design (e.g. a genuinely
  paper-safe connector, or an explicit tiny-size go/no-go per step 8),
  separate from the dry-run work done here.

## Proposed redesign (from this spike's findings — NOT implemented, deliberately deferred)

The spike's results suggest a set of architecture changes about what the
"Condor assistant" even *is* once external harnesses are first-class.
**Status: proposal only.** Implementation was explicitly deferred — the
`assistants/` consolidation touches too many surfaces (Telegram handlers,
web routes, memory paths, the main-process watcher) to bundle with the
spike, and the spike's key questions don't depend on it.

### 1. The coordinator is not an agent — dissolve `assistants/` into `CONDOR.md`

`agents/` is for agents users build (`market_making_expert` is an example,
`_defaults` the template). The coordinator is neither consultable nor
delegatable, holds no strategies, and — as this spike demonstrated —
ceases to exist entirely on external harnesses, where the harness's own
main agent plays that role. Modeling it as an agent would mean permanent
carve-outs (excluded from consult routing, from `manage_trading_agent`,
from agent_builder flows). Instead:

- **`CONDOR.md` at the repo root** becomes the single source for the
  coordinator persona. The name is deliberate: `CLAUDE.md`/`AGENTS.md` are
  harness workspace-instruction conventions and would possess *development*
  sessions in this repo with the trading persona ("Do NOT explore the
  codebase" is exactly wrong for a dev session). `CONDOR.md` is loaded only
  where Condor's own code chooses to load it.
- **Delete the `assistants/` subsystem**: `discover_assistants`,
  `_assistant_cache`, `AGENT_MODES`/`normalize_mode`, the separate
  `main.py` watcher path, and the `paths.py` special case all exist to
  serve exactly one folder (FEAT-004 already collapsed the modes).
- "Condor the assistant" = **this repo + its MCP servers, opened from any
  harness**. The coordinator is emergent behavior, not a runtime object.

### 2. The harness owns the persona; MCP instructions carry only mechanics

`CONDOR.md`'s full persona must NOT ride the MCP server instructions.
External harnesses have their own identities (OpenClaw's `IDENTITY.md`/
`SOUL.md`, Claude Code's own persona) and injecting "You are Condor" would
fight the host. The spike proved the split empirically: gemini-on-OpenClaw
executed the full protocol correctly having received only the routing rule,
zero persona. Layering:

- **MCP server instructions** (`_build_instructions()`): coordination
  mechanics only — routing rule (skill → agent → raw tools),
  consult/delegate contract, safety rules. Harness-agnostic, identity-free.
- **`CONDOR.md`**: full persona, loaded only by first-party surfaces
  (Telegram/web system prompt via `_build_system_prompt()`), where there is
  no host persona to defer to.

### 3. Skills go harness-native; one canonical directory

Condor's skill format (`<name>/SKILL.md`, `name`/`description`/
`when_to_use` frontmatter) is already the same shape all three harnesses
use natively: Claude Code (`.claude/skills/`), OpenClaw (`openclaw skills
install` from a local dir; ClawHub), Hermes (agentskills.io standard). So:

- Keep **one canonical skills directory** in the repo; expose it natively
  per harness (symlink into `.claude/skills/`, `openclaw skills install
  <path>`, drop-in for Hermes). Native progressive disclosure replaces the
  "call `manage_skill(action='read')` before known flows" instruction and
  is a strictly better UX.
- Skill→routine linkage survives untouched: skill bodies instruct MCP calls
  (`manage_routines(action="run", ...)`), which this spike proved work from
  any harness.
- `manage_skill` remains only for surfaces with no native mechanism:
  first-party Telegram chat and domain agents ticking inside `main.py`
  (whose agent-scoped skill stores are separate and unaffected).

### 4. One memory store, Condor's — harness-native memory is not Condor's concern

Telegram is just a gateway into whichever brain the user runs (OpenClaw's
Telegram channel, or Condor's bot fronting `main.py`) — one brain, multiple
doors, all on the local filesystem where the process runs. So there is no
multi-brain sync problem and no reason for a memory taxonomy:

- **`manage_memory` stays as the single Condor memory store.** Its
  consumers are structural, not preference: tick prompts inject it fresh
  each tick in `main.py`, `get_user_context` serves it, Telegram
  first-party chat reads it. It is the trading system's memory and lives
  where the trading system runs. Its home moves out of `assistants/` with
  the consolidation (repo-root `store/` or `.condor/`).
- **Harness-native memory is left completely alone** — not integrated, not
  fought, not documented as a tier. Harnesses jot their own session notes
  regardless; that's their working memory, invisible to Condor and
  harmless.
- One line in the MCP instructions does the routing: "save durable trading
  facts with `manage_memory`" — the same nudge the tick prompt already
  contains.

## Identity across harnesses: the tiered plan (Tier A ships in this PR)

QA hit the identity problem in the wild: a stock `claude` session using the
repo's identity-less `.mcp.json` got an opaque `403 Access denied` on every
consult/delegate — the MCP server defaulted to user id 0 and minted a JWT
for a nonexistent account. Fixing that properly forced the question: how
should identity work when we can't depend on Telegram to assert it?

**The grounding fact that shapes everything below**: the JWT signing secret
lives in `config.yml` on the same filesystem (`condor/web/auth.py`), so
anyone who can read the repo can mint a JWT for *any* user id. The numeric
user id was never a credential — Telegram just asserts it honestly. Locally
there is no security boundary to defend; the design goal is picking the
right identity *automatically*, and introducing a real credential only
where a real boundary exists.

**Rejected: harness-level identity (e.g. "use the OpenClaw id").** In local
mode a harness's identity is itself just self-declared ambient state — zero
security gain over an env var — and it couples Condor to one harness's
identity model, against the harness-agnostic thesis. The harness should
*carry a Condor-issued identity/credential*, not substitute its own.

### Tier A — single-user auto-bind (✅ this PR)

When the MCP server starts with no identity (no `--user-id`/`--chat-id`
args, no `CONDOR_USER_ID`/`CONDOR_CHAT_ID` env) and `config.yml` has
**exactly one approved user**, bind to it and log the choice
(`settings.ensure_identity()`, called at server startup and defensively
from `call_main_api`). Resolution order: CLI args > env vars > auto-bind.

- Zero setup for the dominant case (single-user local install): any harness
  pointed at the repo just works — no env vars, no 403.
- No trust change: the OS user launching the harness already holds every
  secret on disk. Auto-bind is a *selector*, not authentication.
- With zero or multiple approved users, nothing is bound and the fail-fast
  error (also this PR) names the exact fix — a multi-user box must say who
  it is explicitly.

### Tier B — `condor init` pairing with a stored credential (later: when one box genuinely has multiple users)

The `gh auth login`/`aws configure` pattern: a one-time command generates a
random token, stores it at `~/.condor/credentials` (0600, outside the repo
so it can't be committed), and registers it against a user in `config.yml`.
The MCP server reads it as another resolution-order step; the main API
verifies the **token**, not just a self-minted JWT — the first point at
which per-user identity on a shared box becomes real (distinct secrets,
revocable). Telegram ids become linked aliases of the account rather than
the primary key.

**Trigger to build it**: the first genuine multi-user-on-one-machine need —
a shared team box, or Portal-style onboarding that registers more than one
user. Not before: for single-user installs it's pure ceremony on top of
Tier A, and the env-var escape hatch already covers the rare multi-user
box until then.

### Tier C — MCP OAuth 2.1 (only when Tier 2 leaves the machine)

**Yes — Tier C is specifically for the hosted/cloud deployment** (the
roadmap's Phase 0 hosted box), or any topology where the harness and the
Condor main process are on different machines. That's the first point where
a network boundary — and therefore real authentication — exists. The MCP
transport becomes HTTP, and the MCP authorization spec (OAuth 2.1) is the
ecosystem-standard mechanism: the harness pops a browser, the user logs
into Condor's portal, and a scoped token is issued. Claude Code and peers
already do this dance natively for remote MCP servers, so the UX is
*better* than env vars, not worse. Local installs never need Tier C, no
matter how many harnesses they use.

### Net

Identity becomes harness-independent at every tier — Telegram, Claude Code,
OpenClaw, Codex, Hermes all resolve the same Condor account through the
same chain (explicit args > env > credential file (B) > auto-bind (A)), and
each tier is added only when the trust situation it addresses actually
appears: A now (UX, no security pretense), B at multi-user-on-one-box, C at
remote/hosted.

## QA instructions: reproducing this spike per harness

Everything below assumes the QA machine has the condor repo checked out and
working (`uv sync` done, `config.yml` present). All harnesses connect to the
same two stdio MCP servers, launched **from the repo directory** — `uv run`
resolves the project venv from the cwd, so either open the harness in the
repo dir or use `uv run --directory /path/to/condor ...` in the server args.

### Prerequisites (all harnesses)

1. **The Condor main process must be running** (`make run` in the repo dir).
   Tier 2 lives there: `start_agent`/`stop_agent`/`list_agents`/`consult`
   all forward to its web API on `127.0.0.1:8088` (`WEB_PORT`). Without it,
   lifecycle calls return API errors — MCP tools alone do not carry Tier 2.
   **Corollary: after pulling code changes, restart it** — fixes on disk do
   not reach the running process (learned twice in this spike).
2. **A Hummingbot API server** reachable (default `localhost:8000`) and
   registered in `config.yml`.
3. **Identity values** — you need three, all from `config.yml`:
   - `CHAT_ID` / `USER_ID`: a registered user id (e.g. `admin_id`)
   - `SERVER_NAME`: a server entry name the user can access (e.g. `local`)
4. ⚠️ **Capital check before anything else**: run a read-only
   `get_portfolio_overview` and look at what the configured server holds.
   If it's a live account, use `execution_mode="dry_run"` ONLY, and never
   `loop`/`run_once`. The dry-run gate blocks `manage_executors`,
   `manage_bots` mutations, `place_order`, and gateway swaps — but treat it
   as a seatbelt, not an invitation.

The two servers, in the shape every harness needs (adjust identity):

| server | command | args |
|---|---|---|
| `condor` | `uv` | `run python -m mcp_servers.condor --chat-id <CHAT_ID> --user-id <USER_ID> --server-name <SERVER_NAME>` |
| `mcp-hummingbot` | `uv` | `run python -m mcp_servers.hummingbot_api --url http://localhost:8000 --username <U> --password <P> --server-name <SERVER_NAME>` |

Expected on connect: **12 tools each**. The condor server also delivers
routing instructions (skill → agent → raw tools) that the harness's agent
should visibly follow.

### Claude Code — tested ✅

Interactive: `cd /path/to/condor && claude` — the repo's own `.mcp.json`
registers both servers. Identity: on a **single-user install** (one
approved user in `config.yml`) nothing is needed — Tier A auto-bind picks
that user. On a multi-user box, export your registered id first (shell
profile; the MCP subprocess inherits Claude Code's environment):

```bash
export CONDOR_USER_ID=<your registered user id>   # e.g. config.yml's admin_id
export CONDOR_CHAT_ID=<same id>
```

**Known failure mode** (hit in QA, pre-Tier-A): `consult`/`delegate`/
lifecycle calls failed with an opaque `403 Access denied` — the server
minted a JWT for the default user id 0, which isn't a registered account
(same auth as the web dashboard). Now: single-user installs auto-bind;
ambiguous configs fail fast with an error naming these env vars. Note this
is Condor-user identity, unrelated to the hummingbot-api's admin/admin
credentials.

Headless / isolated (what this spike used — avoids touching repo state):
write the two servers into a scratch `qa-mcp.json` (identity as explicit
`--chat-id`/... args), then:

```bash
claude -p --mcp-config qa-mcp.json --strict-mcp-config \
  --allowedTools "mcp__condor__manage_trading_agent" \
  "Call manage_trading_agent with action='list_agents' and report the raw JSON."
```

### OpenClaw — tested ✅

```bash
openclaw mcp add condor --command uv \
  --arg run --arg python --arg -m --arg mcp_servers.condor \
  --arg --chat-id --arg <CHAT_ID> --arg --user-id --arg <USER_ID> \
  --arg --server-name --arg <SERVER_NAME> \
  --cwd /path/to/condor --timeout 240
openclaw mcp add mcp-hummingbot --command uv \
  --arg run --arg python --arg -m --arg mcp_servers.hummingbot_api \
  --arg --url --arg http://localhost:8000 \
  --arg --username --arg <U> --arg --password --arg <P> \
  --arg --server-name --arg <SERVER_NAME> \
  --cwd /path/to/condor --timeout 240
openclaw mcp probe    # expect: condor: 12 tools; mcp-hummingbot: 12 tools
openclaw agent --local --agent main --session-key condor-qa \
  -m "Call manage_trading_agent action='list_agents' and report the raw JSON."
```

**`--timeout 240` is required, not optional**: OpenClaw's default
per-request MCP timeout (~60s) kills `consult`/`delegate`-class tools
(a consult runs a full worker session, 1–3 min; the tool's own budget is
180s) with `MCP error -32001: Request timed out`.

Gotchas hit during this spike: the `main` agent needs a working model
credential in its own auth store (`openclaw models status` to check;
`openclaw doctor --repair` fixed a migration gap here); the gateway
LaunchAgent may need `openclaw doctor --repair` + `openclaw gateway
install` after a CLI upgrade (though `--local` embedded runs work without
the gateway).

### Codex — NOT yet tested, expected shape

Codex reads MCP servers from `~/.codex/config.toml`. There is no per-server
cwd, so use `uv --directory` to pin the project:

```toml
[mcp_servers.condor]
command = "uv"
args = ["run", "--directory", "/path/to/condor", "python", "-m", "mcp_servers.condor",
        "--chat-id", "<CHAT_ID>", "--user-id", "<USER_ID>", "--server-name", "<SERVER_NAME>"]

[mcp_servers.mcp-hummingbot]
command = "uv"
args = ["run", "--directory", "/path/to/condor", "python", "-m", "mcp_servers.hummingbot_api",
        "--url", "http://localhost:8000", "--username", "<U>", "--password", "<P>",
        "--server-name", "<SERVER_NAME>"]
```

Then run `codex` and drive the same checklist below. Check Codex's MCP
request timeout — if configurable and < 240s, raise it (same consult issue
as OpenClaw). Record results in this doc.

### Hermes — NOT yet tested, expected shape

Hermes-agent has first-class MCP client support (auto-reconnect, per-server
timeouts — see fork-vs-build.md); registration is a one-block
`mcp_servers:` entry in its config using the same command/args shape as the
table above (use `uv run --directory /path/to/condor ...` if its config has
no cwd field). Set its per-server timeout ≥ 240s. Consult Hermes docs for
the exact config schema; record results in this doc.

### The QA checklist (same for every harness)

Run these in order; each step's expected result is exact:

1. **Connectivity**: both servers connect, 12 tools each.
2. **Read state**: `manage_trading_agent(action="list_agents")` → valid
   JSON (`{"agents": [], "message": "No agents running"}` if idle).
3. **Start**: `action="start_agent"`,
   `strategy_id="market_making_expert.pmm_mister_operator"`,
   `config={"execution_mode": "dry_run"}` → `{"started": true,
   "agent_id": "..._eN"}`. Note: `strategy_id` must be the **full
   `agent.strategy` key** — a bare `pmm_mister_operator` returns
   "not found".
4. **Observe**: immediate `list_agents` → the agent with
   `status: "running"`, `execution_mode: "dry_run"`. Dry-run is
   **one-shot**: after ~2–3 min the tick completes, the agent disappears
   from `list_agents` (this is expected, not instability), and
   `agents/market_making_expert/strategies/pmm_mister_operator/dry_runs/experiment_N.md`
   exists, ending with "No executors were created (dry run)".
5. **Cross-harness**: start another dry_run from harness A, then within
   ~2 min from harness B: `list_agents` shows it; `stop_agent` with its
   agent_id → `{"stopped": true}`. (The window is short — script harness
   A's start so B fires immediately.)
6. **Consult**: `consult(agent="market_making_expert", task="quick regime
   read for <pair> on <connector>, advisory only")` → a domain analysis
   that takes no deploy action. Requires client MCP timeout ≥ 240s.
7. **Create loop**: `create_agent` (any test slug) → `consult` it (alive?)
   → `create_strategy` → `start_agent` dry_run → snapshot written under
   the new agent's dir. Clean up with `delete_strategy`/`delete_agent`.
8. **Routine authoring**: `consult(agent="routine_builder", task="create a
   routine named <x> for agent <slug> that ...")` → file appears under
   `agents/<slug>/routines/`, and the builder's answer shows it read its
   `routine_cookbook` skill (if it says the cookbook is unavailable, you
   are running pre-#152 code — restart the main process). Then
   `manage_routines(action="run", name="<x>",
   strategy_id="<slug>.<strategy>")` executes it.
9. **Safety spot-check** (dry_run integrity): during any dry-run tick,
   `manage_bots(action="status")` before vs after shows no new bot;
   portfolio unchanged. The tick prompt (visible in the `experiment_N.md`
   snapshot header) must contain the manage_bots blocking language — if it
   shows only "Do NOT create or stop executors", the backend is running
   pre-#151 code; restart it.

**Prompting tip for weaker/looser models**: give one precise tool call per
turn ("Call manage_trading_agent with EXACTLY these params ... report the
raw response"). In this spike, a multi-step turn led gemini to misread the
one-shot agent's expected self-stop as instability and abandon its steps.
