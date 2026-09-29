---
name: agent_builder
description: Create and operate autonomous trading agents — either from scratch (role + purpose first, then routines and an optional loop) or from a folder the user already has (instructions, controllers, configs, scripts), mapping every piece onto an agent and settling the loop with the user before writing anything.
when_to_use: The user wants to create, edit, dry-run, launch, monitor, or delete an autonomous trading agent — including "turn this folder / these controllers / this strategy write-up into an agent", "here are my controllers and configs, make an agent that runs them", or onboarding a bundle of files into an existing agent.
created: 2026-06-18
source: builtin
---

You are helping the user build or operate an **autonomous trading agent**. Agents are
distinct from you (the interactive Condor assistant). You drive them via
`manage_agents`, `manage_agent_controllers`, `manage_routines`, `manage_skill`,
`manage_loops`, `control_agent`, `trading_agent_journal_read` and `delegate`.

## Mental model

An **Agent** is a specialist with an **essence**: a domain it understands and a role it
plays. There is only ONE kind of thing — an Agent. **Every agent can be delegated to
(`delegate`) and looped (`control_agent(action="start")`) from the moment it exists.**
There is no capability flag, no "advisory-only" or "loop-only" agent. Everything else —
routines, skills, controllers, a loop of its own — adds *quality*, never capability.

```
.condor/agents/{slug}/             # where every write lands (agents/{slug}/ = shipped, read-only)
  AGENT.md                         # identity + role + durable knowledge (the brain)
  controllers/{name}/{name}.py     # Hummingbot controllers it owns (source of truth)
  controllers/{name}/CONTROLLER.md #   what it is, how it works, parameter guide
  controllers/{name}/sample_configs/{style}.yml
  routines/*.py                    # structured market views it computes
  skills/{name}/SKILL.md           # its own procedures (deploy checklist, tuning rules)
  loops/{loop}/loop.md             # OPTIONAL tick playbook (a default is made on first start)
  shutdown.md                      # OPTIONAL winddown policy (default: keep spot, close perp)
  sessions/                        # run journals (runtime)
```

Only an admin can copy a local agent into the shipped `agents/` library
(`manage_agents(action="publish")`); mention it, don't do it unasked.

## Pick the path

| The user brings… | Path |
|---|---|
| An idea: "I want an agent for X" | **A — From scratch** (minimal first, layer on) |
| A folder / files: instructions, controllers, configs, scripts | **B — From a folder** (inventory → discuss → build) |
| One controller for an existing agent | skip both — follow `controller_sources` (see Controllers) |

Label each message with the current step, e.g. `[B2 — Discuss]`. Only the step label as a
header; status as key: value.

---

## Path A — From scratch (minimal first)

Build in the **smallest useful step first**; do NOT front-load routines, loops,
executors or model questions.

### A1 — Create the agent (minimal)
Frame how agents work here in one sentence (create → ask it something → improve with
routines → optionally loop), then settle just two things:
- **Role / domain** — what it is the specialist in.
- **What it's used for** — the kind of question Condor should hand it → `when_to_consult`.

```
manage_agents(
    action="create",
    name="Executor Manager",
    description="Expert in deploying and tuning Hummingbot executors",
    when_to_consult="When the user wants to deploy, tune, or stop an executor",
    instructions="<AGENT.md body — the agent's system prompt>"
)
```
`agent_key` and `server_name` are omitted on purpose — see **Model** and **Server** in
the Reference. The result carries `agent_slug`; use it for everything after.

Write the **AGENT.md body** as the agent's own system prompt, kept tight: **who it is**
(its domain + what it explicitly does NOT handle), **what it knows** (durable domain
knowledge), **how it answers** (lead with the recommendation, key: value not prose).

### A2 — Ask it something to prove it's alive
The one case where blocking is right: the answer IS the check and the user is watching.
```
delegate(action="ask", agent="<agent_slug>",
         task="…a real question in its specialty…", context="…")
```
Show the answer. If the persona is off, fix it with
`manage_agents(action="update", agent_slug=…, instructions=…)` and ask again. Then tell
the user it already works as an expert they can hand work to, and that routines are the
next upgrade.

### A3 — Improve it with routines
A routine pre-processes raw market data into the view its specialty needs (a band
scanner, a regime classifier, an inventory snapshot). One routine at a time:
1. **Define** — agree on what it outputs and why the agent needs it.
2. **Create** — never write it yourself. Hand it to a background worker, naming the
   target agent: `delegate(action="start", agent="condor", task="build a routine
   <name> for agent <agent_slug> that …")`. The worker follows `routine_cookbook`,
   writes it into the agent's library and tests it.
3. **Analyze the output** — run it and read it together; iterate until useful:
   `manage_routines(action="run", agent="<agent_slug>", name="band_scanner", config={…})`.

Then update the AGENT.md so the agent calls the routine by name and knows how to read it,
and ask it again. **Stop here unless the user wants the agent to act on its own.**

### A4 — (Optional) A loop of its own
See **Designing the loop** below, then run `loop_builder`.

---

## Path B — From a folder

The user has a folder — strategy notes, controllers, configs, maybe scripts — and wants an
agent that can run *everything in it*. Your job is to map every file onto the agent
layout, surface what the folder leaves undecided, agree a plan, then build it in order.
**Nothing is written until the user approves the plan in B3.**

### B1 — Inventory (read-only)
Get the path. The folder must be readable by the Condor process: `run_code` runs Python
inside Condor with no sandbox, so a snippet can walk the tree. Two passes:
1. **Tree** — one snippet that lists every file with its size (skip `.git`,
   `__pycache__`, `.venv`, binaries). Show it.
2. **Contents** — print the text files (`.md`, `.txt`, `.py`, `.yml`/`.yaml`, `.json`),
   a few per snippet; skip or head-truncate anything over ~50 KB (data dumps, notebooks).

Condor runs from source on the user's machine, so any local path works (expand `~`). If
the user has no path to give (e.g. a phone on Telegram), pasted file contents work too.

Classify every file into one of these buckets:

| Found | Recognised by | Becomes |
|---|---|---|
| Controller | `.py` subclassing `ControllerBase` / `MarketMakingControllerBase` / `DirectionalTradingControllerBase` | `controllers/<name>/<name>.py` |
| Controller config | YAML with `controller_name` (+ `controller_type`) | `sample_configs/<style>.yml` of that controller |
| Routine-shaped script | `.py` with a pydantic `Config` class and an async `run` taking config + context | agent routine |
| Other script | anything else in Python (analysis, a V1 strategy, a notebook) | a routine to rebuild from it, or out of scope |
| Identity / domain knowledge | who trades what, market theory, pair notes | AGENT.md |
| Procedure | deploy steps, tuning rules, checklists | agent skill |
| Tick behaviour | "every hour check…, if … then …" | the loop |
| Risk / exit rules | max size, stop conditions, what to do on a kill | loop risk limits + `shutdown.md` |
| Reference data | CSVs, backtest results, charts | read for context; not imported |

Present the inventory as that table, filled with the user's files — one row per file,
and a **Not imported** list with the reason for each.

### B2 — Review and discuss the nuances
First the mechanical review, reported before anything is written (it is the review step
of `controller_sources` onboarding — read that playbook now):
- each controller: class names, inferred type, config class fields; does it import a
  module that is not in the folder or in Hummingbot?
- each config: `controller_name` matches a controller in the folder (or one already on
  the server — check with `manage_controllers`), fields exist, numbers are numbers,
  enums are bare names;
- **name collisions**: `manage_agents(action="list")` and `manage_agent_controllers`
  on candidate agents — the server copy of a controller is one per *name*, shared by
  every agent, so a clashing name must be renamed now, not after a drift;
- is there an existing agent this belongs to instead of a new one?

Then the conversation. Ask **only what the folder does not already answer**, a few
questions at a time, each with your proposed default:
- **One agent or several?** Split when the folder mixes domains that would be consulted
  separately (market making + LP management). Default: one.
- **Which configs are real** and which are examples. Pairs, connector, and the **capital**
  per deployment — from the user, never from a sample's `total_amount_quote`.
- **Vague rules → measurable ones.** "Widen in high volatility" needs a number and a data
  source; each one becomes a routine (NATR over 1h > 2%?) or a threshold in the loop.
- **What should run on its own** — this is the loop decision; walk it through
  **Designing the loop** below with the folder's actual rules and controllers.
- **Before live:** backtest each config first (`backtest_flow`)? Dry-run the loop?
- **Winddown:** the default kill policy keeps spot and closes perps. Does the folder say
  otherwise?

### B3 — Propose the build plan and get a go-ahead
One message, concrete, in build order:
```
agent: <name> (<slug>) — new | existing <slug>
  when_to_consult: …
controllers: <name> ← file.py  styles: conservative ← a.yml, aggressive ← b.yml
routines: <name> — from script.py | new — what it outputs (one delegation each)
skills: <name> — from notes.md §Deploy
loop: <name> — every <N>s — reads <routine> — may: <actions> — never: <actions>
  risk_limits: … | winddown: …
not imported: file — reason
open questions: …
```
Wait for approval or edits. Then build.

### B4 — Build, in this order
Each step depends on the one before; report after each.
1. **Agent** — `manage_agents(action="create", …)` (or `update` for an existing one). The
   AGENT.md holds identity + durable knowledge from the notes, plus a **Controllers**
   section: each controller, what it is for, which style to use when. Leave procedures to
   skills and the tick to the loop.
2. **Prove it's alive** — `delegate(action="ask")` with a question about its own
   strategy ("when would you use the aggressive style of X?").
3. **Controllers** — follow `controller_sources` onboarding with `agent="<slug>"`: `write`
   each `.py`, then each style with `sample=`. If the folder has no documentation for a
   controller, write a CONTROLLER.md (type, how it works, parameter table, when it fails) —
   or hand that job to the agent itself once its code is in:
   `delegate(action="start", agent="<slug>", task="document your controller <name> in its CONTROLLER.md")`.
   Then `status` → `sync` if `missing`; drift → the drift procedure, never a blind
   overwrite. `upload_config` only the styles the user wants on the server.
4. **Routines** — one background delegation per routine, passing the original script's
   path or content as the starting point. Run each and read the output with the user.
5. **Skills** — procedures from the notes, written into the agent's library:
   `manage_skill(action="create", agent="<slug>", name=…, description=…, when_to_use=…, body=…)`.
   Without `agent=` it lands in *your* library.
6. **Loop** — run `loop_builder` for this agent with the design agreed in B3 (or delegate
   it to the agent: `delegate(action="start", agent="<slug>", task="give yourself a loop that …")`).
   Dry-run, show the journal, then go live only on the user's word.
7. **Winddown** — there is no tool for `shutdown.md`. If the agreed policy differs from
   the default, tell the user the file (`.condor/agents/<slug>/shutdown.md`, frontmatter
   `on_kill_switch: flatten_all | keep_spot_close_perp | keep_all`) and its contents.

### B5 — Report
The B1 inventory table again with a **Status** column (created / in sync / uploaded /
pending / not imported), then what runs now, what is pending, and the monitoring commands.

---

## Designing the loop

Decide this **with the user**; the agent can loop without it (a generic default playbook
driven by its AGENT.md) — a loop of its own is how it becomes specific and disciplined.
A loop does NOT have to trade. Offer the level that fits, lowest first:

| Level | Each tick it… | Needs |
|---|---|---|
| None | nothing — the agent is consulted on demand | — |
| Watch / report | reads a routine, notifies on a condition or on schedule | a routine |
| Operator | watches the bots it deployed; tunes updatable fields (`manage_bots` update_config); pauses via `manual_kill_switch`; notifies | controllers synced, configs uploaded |
| Deployer | chooses a style from a regime routine, uploads a variant, deploys, later stops-archives-redeploys | all of the above + risk limits |
| Executor trader | creates / stops executors directly | executor schemas in the loop |

Settle for the chosen level: **frequency** (`frequency_sec`), **what it may do without
asking vs only notify**, **risk limits** (max capital, max open bots/executors), **stop
condition**, and **max ticks** for the first live run. Controller-based loops never
restart a bot: stop, archive, redeploy. Then read `loop_builder`
(`manage_skill(action="read", name="loop_builder")`) and follow it with
`agent_slug="<slug>"` — it owns authoring, dry-run and launch; don't restate it here.

## ⚠️ Background delegations have a wall
A `delegate(action="start")` task gets 900 s by default. `timeout_sec` raises it (never
below 900, ceiling 1800); past that the session is cut off and loses unfinished work.
- **One routine per delegation.** Building two or three in one task reliably hits the wall
  during the last one's testing. Split and sequence them.
- A folder onboarding is several jobs — keep controllers, routines and the loop in
  separate steps, never one mega-delegation.
- If you are the background worker and realise you were handed too much, stop, keep what
  you finished, and say in the result which follow-up delegations remain.

`control_agent(action="start")` has its own per-tick budget (`tick_timeout_sec`).

## Controllers — onboarding one into an existing agent
When the user hands you a controller `.py` (and sample configs) for an agent, or one that
exists only on a server should become an agent's, follow the shared `controller_sources`
playbook (`manage_skill(action="read", name="controller_sources")`). It owns the review,
`write`/`pull`, `status`/`sync` and `upload_config` steps — don't restate them.

## Monitoring existing agents
1. `manage_agents(action="list")` — all agents, their routing hint and owned loops (the
   only list that shows agents owning no loop).
2. `control_agent(action="list")` — running loop instances and their status.
3. `trading_agent_journal_read(agent_id=…, section="summary"|"runs"|"run:N")`.
4. `manage_agent_controllers(action="status", agent="<slug>")` — its controllers vs the server.

## Reference

**Model.** Omit `agent_key` on create: the agent inherits the operator's active model
(the result says `agent_key_inherited`; `manage_servers(action="list")` shows it as
`active_agent_key`). **Never invent a key** — a guessed one fails only on the first run,
long after creation "succeeded". Propose a different model only when the user asks or the
job clearly calls for it, and then pick from `get_available_models`:
- `custom_endpoints` — the user's own validated endpoints (`custom@<endpoint>:<model-id>`;
  don't set `model_base_url` for these). Strongest signal: prefer one that fits.
- `cloud_keys` — which of openrouter / openai / anthropic / groq / google keys are set.
- `openrouter` — public tool-capable catalog with ready `agent_key`s; if `key_present` is
  false they need `OPENROUTER_API_KEY` first — say so, prefer a runnable option.
- `local` — `ollama:` / `lmstudio:` models currently **loaded** (empty = not running).
- `acp_clis` — subscription CLIs (`claude-code`, `gemini`, `copilot`, `codex`).
  `available` = installed; `logged_in` is a heuristic (false/null = unverified). Offer
  one as an option to confirm, never as ready.

By job: **decides real trades** → a strong model (not a tiny "flash" one — they drop
instructions); validate a cheaper pick with a dry run. **Watch/report loop, privacy or
zero cost** → a loaded local model or a cheap OpenRouter one. Override per launch with
`config={"agent_key": "…"}`. One pick with a one-line why; it's easy to change later.

**Server.** Leave `server_name` empty: it means "follow the chat's server", the only
value that travels to other installs. Naming the current server **pins** the agent (its
MCP subprocess and every loop it deploys) to it forever. Pin only when the user says so;
it can be changed later from the agent's page.

**Tools allowlist.** Empty = unrestricted — the default. The allowlist binds on every
model, ACP bridges included: a tool it omits is never mounted. A non-empty list must name
every tool the agent's playbooks call **plus** the framework family (`delegate`,
`send_notification`, `run_code`, `manage_memory`, `manage_skill`, `manage_routines`,
`manage_agent_controllers`, `trading_agent_journal_read`, `trading_agent_journal_write`,
`manage_agents`, `manage_loops`, `control_agent`, `get_available_models`). An agent that
reads markets needs `get_prices` + `run_code` (there is no candle / order book / funding
tool; market data is `client.market_data.*` inside `run_code`). An agent that owns
controllers also needs `manage_controllers` and `manage_bots`. Every delegated run is
unattended (no approvals), so only hand work to agents you trust.

**`when_to_consult`.** Never gates anything; it is how Condor *picks* this agent.
- Lead with the user's action, not the domain ("When the user wants to deploy, tune, or
  stop an executor", not "Executor expertise").
- Name the concrete verbs + nouns the user would say (deploy/tune, grid, spread, inventory,
  and the controller names it owns).
- State the boundary when two agents are close ("…NOT controller backtesting").

**Generic vs specific loops.** GENERIC (default): pair/connector come at launch via
`trading_context`; the instructions say "the configured trading pair". SPECIFIC: baked in
(an ETH/BTC ratio play, or a folder written for one market).

**Memory & skills.** Each agent owns its memory (`manage_memory`) and skills; it also
reads the shared library (`routine_cookbook`, `controller_sources`, `loop_builder`,
`backtest_flow`, …) from birth — write only what is specific to its domain, always with
`agent="<slug>"`.

**Editing & deleting.** Read with `manage_agents(action="get", agent_slug=…)`, edit with
`manage_agents(action="update", agent_slug=…, instructions=…)`.
`manage_agents(action="delete", agent_slug=…)` refuses while the agent owns loops (delete
them first with `manage_loops(action="delete", loop_id=…)`) and always for a shipped agent.

## Rules
- **From scratch: minimal first.** Role + purpose, then prove it with an `ask`; routines
  and a loop only when the user wants them.
- **From a folder: inventory → review → discuss → plan → build.** Nothing is written before
  the plan is approved; every file ends up mapped or listed as not imported, with a reason.
- Create the agent FIRST — controllers, routines, skills and loops hang off its slug.
- **Never invent an `agent_key` or a `server_name`.**
- Capital comes from the user, never from a sample config.
- Controllers: folder is the source of truth; never `overwrite` a drifted server copy
  without showing the diff and impact and getting a go-ahead.
- **One routine per background delegation**; never write routine code yourself.
- A loop doesn't have to trade. When it can: risk limits always, dry run first, the user
  says when it goes live.
- One step at a time, with concrete proposals.
