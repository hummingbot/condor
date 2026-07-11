# Refactor 04 — Shared skills tier

Status: **tabled** (2026-07-11) · Branch: `spike/simpler-agent-framework`

## 0. Decision: tabled, not needed yet

Deferred by decision. Rationale: the problem it solves (knowledge shared
across agents) has workable interim answers — skills can be pulled in from
elsewhere, or shared knowledge can live at the root agent level — and the
mechanism is **purely additive**: `agents/_shared/skills/` can land at any
later point without breaking or migrating anything (an empty shared tier is a
no-op; existing local skills and agent bodies are untouched). Nothing in
refactors 01–02 depends on it.

Two consequences of tabling, reassigned:

1. **Refactor-01's §7 capability loss stands unmitigated for now** — a second
   agent in the same domain copies knowledge. Acceptable until a second such
   agent actually exists. (Further reduced by the pivot to
   [refactor-01b](refactor-01b-agent-history-multi-strategy.md): a second
   *playbook* in the same domain shares the agent's brain without any copying
   — only a genuinely separate agent still copies.)
2. **The mm_expert duplication (§2.2) still needs reconciling** — refactor-01's
   merge concatenates AGENT.md and strategy.md into one body, putting the two
   *conflicting* pmm_mister parameter guides in the same file. That
   reconciliation now happens as an editorial step of refactor-01's mm_expert
   rewrite (verify against `manage_controllers(action="describe")`), not here.

The proposal below is preserved as the record of the considered design; the
mm_expert decomposition demo (§4) remains the blueprint for whenever the tier
is picked up.

---

Original proposal — independent mechanism; directly resolves the one real
capability loss in [refactor-01](refactor-01-agent-strategy-merge.md) §7 ("a
second playbook means its domain knowledge must be **copied**"). The demo
below assumes refactor-01's merged AGENT.md so the mm_expert body is only
rewritten once.

## 1. Goal

Add **one shared skill library that every domain agent reads in addition to its
own** — `agents/_shared/skills/` — so domain knowledge is written once,
version-controlled once, and *progressively disclosed* (index line always in
context, body pulled on demand), instead of being:

- baked into an agent's AGENT.md body, where it is injected into **every** tick
  and consult prompt whether needed or not, and
- copied between agents (or between an agent and its strategy file), where the
  copies drift.

Demonstrate by decomposing `market_making_expert`: its AGENT.md body splits into
three shared skills — **market_making**, **bot_deployment**, **pmm_mister** —
after which what remains in AGENT.md is essentially today's
`pmm_mister_operator/strategy.md`: a thin identity plus the operator playbook.

## 2. Today: per-agent silos, and the drift they already caused

### 2.1 Scoping

`SkillStore` is keyed by a single slug (`condor/memory/skills.py:80`,
`paths.builtin_skills_root`); every operation — `read`, `search`,
`list_index`, `create` — sees exactly one directory:

```
assistants/condor/skills/        <- chat condor only   (agent_slug=None)
agents/market_making_expert/skills/   <- mm_expert only
agents/routine_builder/skills/        <- routine_builder only
                                      (no tier is shared across agents)
```

`agent-framework.md` §3 already flags this: *"'Shared' in the code's own
docstring means shared across the users of one assistant — not shared across
assistants/agents."*

### 2.2 The duplication is not hypothetical

The pmm_mister parameter knowledge exists **three times** today, and two of the
copies already disagree:

| Fact | `AGENT.md` (param guide, ~150 lines) | `strategy.md` (schema table) |
|---|---|---|
| `total_amount_quote` default | 1000 (`AGENT.md:109`) | 100 (`strategy.md:87`) |
| `portfolio_allocation` default | 0.03 (`AGENT.md:128`) | 0.1 (`strategy.md:88`) |
| `leverage` guidance | default 1, "keep 1–5x" (`AGENT.md:118`) | default 20, "≤10 illiquid, up to 20 majors" (`strategy.md:104,193`) |

The third copy is the `pmm_config_playbook` local skill (vetted profiles as
companion files, `source: builtin`). Nobody decided these should diverge — they
drifted because the same knowledge has three homes and no single owner.

### 2.3 The token shape is wrong

`build_agent_context` and `build_tick_prompt` inject the **full AGENT.md body**
(and today the full strategy body) into every run: ~260 + ~205 lines,
always-in-context, even for a consult that only asks "is my inventory skewed?".
Meanwhile the skills machinery already implements exactly the right shape —
one index line per skill, body on demand, companions one more hop away — and
the agent's prompt already teaches it to use that shape. The knowledge is just
sitting in the wrong container.

### 2.4 And it blocks agent reuse

Refactor-01 accepts one-playbook-per-agent partly because "same playbook,
different market" is covered by launch config. The uncovered case — a second
agent needing the *same domain knowledge* under a *different playbook* (e.g. a
grid-strategy agent that still needs regime detection and deploy mechanics) —
requires copying today. That is refactor-01 §7's one real loss; this tier is
the named fix.

## 3. Target: three tiers, one resolution rule

```
assistants/condor/skills/      chat condor only (unchanged; the coordinator
                               does not read agent domain tiers)
agents/_shared/skills/         NEW — read by EVERY domain agent
agents/{slug}/skills/          agent-local — wins on name collision
```

`agents/_shared/` follows the `agents/_defaults/shutdown.md` precedent and is
already skipped by every agent-dir scanner (`agent.py:214`,
`paths.iter_user_stores` both skip `_`-prefixed dirs) — no scanner changes.

Resolution, for an agent-scoped `SkillStore(slug)`:

```
read(name):        agents/{slug}/skills/{name}   hit -> local skill
                   agents/_shared/skills/{name}  hit -> shared skill
                   else                          -> not found

list_index():      union of both dirs, one line per name;
                   local shadows shared on collision;
                   shared entries marked:   - [pmm_mister] (shared) when...
                   overrides marked:        - [pmm_mister] (overrides shared) when...

search(q):         spans both tiers (shadowed shared skills excluded)

create/edit/delete/write_file:
                   LOCAL tier only, always, for an agent-scoped store.
                   Editing a name that resolves to a shared skill ERRORS:
                     "pmm_mister is a shared skill (agents/_shared/skills/).
                      Create a local skill under a new name, or ask the user
                      to update the shared tier."
                   The shared tier is writable only via an explicit
                   scope="shared" (chat condor / human-driven).

read_file(name, f): resolves in the tier the SKILL.md resolved from.
                   A local override does NOT inherit the shared skill's
                   companions — whole-skill precedence, no cross-tier merge.
```

Notes on the rules:

- **Two-tier read is a documented resolution order** (like the shutdown policy
  walk `agent → _defaults`), not a data fallback — nothing is fabricated on a
  miss; a miss is still an error.
- **No copy-on-write forks.** An agent that wants to refine shared knowledge
  gets a loud error and two explicit paths (new local skill, or propose the
  edit to the user). A silent fork would let one agent's tuning insight
  invisibly diverge the tiers — the exact failure §2.2 documents.
- **`references_routine` is validated per-reader** — `_routine_exists` already
  takes the reading agent's slug, so a shared skill referencing a routine
  reports `routine_ok=true` only for agents that actually ship that routine.
  Advisory, exactly as today. (Recommendation: shared skills should avoid
  routine references; see §9.3.)
- **Chat condor does not read the shared tier.** The coordinator routes domain
  work to agents; injecting MM parameter guides into its prompt is noise. It
  *manages* the tier via `scope="shared"`.

## 4. Demo: decomposing market_making_expert

### 4.1 Before (post refactor-01, pre this refactor)

Refactor-01 merges strategy.md into AGENT.md, so the starting point is one
~430-line body, all of it injected every run:

```
AGENT.md                                    always in context
  identity + what-you-handle  (~30 lines)        every run
  two modes / advisory flow   (~30 lines)        every run
  domain knowledge            (~35 lines)        every run      <- regime, spreads,
                                                                   inventory, fees
  pmm_mister parameter guide  (~150 lines)       every run      <- duplicated w/
  ## Tick Playbook                                                 drift, §2.2
    per-tick steps + regime→param map (~90)      every run
    pmm_mister full schema table (~45)           every run      <- the other copy
    deployment flow + risk rules (~40)           every run

skills/  (local): pmm_config_playbook, pmm_mister_deploy,
                  capital_allocation, mm_bot_report
```

### 4.2 The three shared skills

```
agents/_shared/skills/
    market_making/SKILL.md      <- the domain expertise
    pmm_mister/SKILL.md         <- the controller reference (+ profile companions)
    bot_deployment/SKILL.md     <- the Hummingbot deploy mechanics
```

**`market_making`** — the transferable MM expertise, from AGENT.md's "Domain
knowledge" section: regime classification heuristics (ADX/ATR/BBW thresholds),
spread calibration rules per regime, inventory-skew management, and the
fee-vs-TP reference table ("TP must exceed round-trip fees"). `when_to_use`:
*assessing market regime, choosing spread width/skew, judging inventory risk,
or sanity-checking a take-profit against fees — for any market-making
strategy, any controller.*

**`pmm_mister`** — the **single authoritative** controller reference: the full
parameter guide and the schema table, reconciled (the reconciliation forces
resolving §2.2's conflicting defaults — verify against
`manage_controllers(action="describe", controller_name="pmm_mister")` rather
than trusting either copy, per the dynamic-data rule). How-the-controller-works
(position bands, skew system, executor lifecycle, global TP/SL two-phase
close). Absorbs the existing `pmm_config_playbook` and `capital_allocation`
local skills — both are generic pmm_mister knowledge (one is literally
`source: builtin`); the vetted profile configs come along as companion files.
`when_to_use`: *composing, tuning, or explaining a pmm_mister config.*

**`bot_deployment`** — the controller-agnostic Hummingbot mechanics, extracted
from `pmm_mister_deploy` and the strategy.md deployment-flow section: the
two-config-stores rule (saved controller config vs live bot config — update
both), `manage_controllers`/`manage_bots` verbs, deploy → verify → update →
stop patterns, and the risk-engine contract (deploys must declare a bounded
`max_global_drawdown_quote` or be blocked). `when_to_use`: *deploying,
updating, or stopping any Hummingbot bot/controller — any strategy type.*
This is the skill `funding_rate_watcher`, `revival_trader`, or a future grid
agent reuses verbatim.

What stays agent-local: `mm_bot_report` (it references an **agent-local
routine**, which shared skills cannot portably do) and a slimmed
`pmm_mister_deploy` (the mm-specific delegate choreography; its generic
mechanics moved to `bot_deployment` — or it dissolves into the Tick Playbook
entirely).

### 4.3 After: AGENT.md is the operator playbook

```yaml
---
name: Market Making Expert
agent_key: claude-acp:sonnet
tools: [get_market_data, get_portfolio_overview, manage_executors,
        manage_controllers, manage_bots, search_history, manage_memory, manage_skill]
when_to_consult: ...unchanged...
server_required: true
server_name: moneymaker
loopable: true
default_config: {frequency_sec: 120, total_amount_quote: 500, risk_limits: {...}}
---
# Market Making Expert

You are a market making specialist operating pmm_mister controllers.
Your reference knowledge lives in skills — READ them at the step that
needs them, don't guess:
- regime / spreads / inventory / fees  -> manage_skill(action="read", name="market_making")
- composing or tuning a config         -> manage_skill(action="read", name="pmm_mister")
- deploying / updating / stopping bots -> manage_skill(action="read", name="bot_deployment")

## Two modes
Consulted (advisory): gather data, assess, recommend. Do NOT deploy...
Delegated (deployment): read bot_deployment + pmm_mister, run end-to-end...

## Tick Playbook
trading_pair / connector_name always come from [CURRENT CONFIG]...
Step 1: run routines (market_analyzer, mm_dashboard)
Step 2: assess regime            (skill: market_making)
Step 3: decide DEPLOY | UPDATE | HOLD | STOP
Step 4: execute                  (skills: pmm_mister, bot_deployment)
Regime -> parameter mapping table  (the operator's own tuning policy)
Risk rules / error recovery        (unchanged from strategy.md)
```

Which is the observation this refactor is built on: **once knowledge lives in
skills, what's left in AGENT.md is what strategy.md is today** — identity,
modes, and the per-tick decision procedure. The agent file stops being an
encyclopedia and becomes an operator.

The playbook keeps *policy* (the regime → parameter mapping — that's this
operator's opinionated tuning) and delegates *reference* (what the parameters
mean, what the exchange mechanics are) to skills. Policy is per-agent;
reference is shared. That's the editorial line for every future decomposition.

### 4.4 Token accounting

Every tick/consult prompt: ~430 always-in-context body lines → ~120 body lines
+ 7 index lines. The ~150-line pmm_mister guide loads only on ticks that
actually compose or tune a config (DEPLOY/UPDATE — a minority; HOLD ticks never
pay for it). Consults about inventory skew load `market_making` alone.

## 5. Code change inventory

| File | Change |
|---|---|
| `condor/memory/paths.py` | `shared_skills_root() -> agents/_shared/skills/`. Docstring updates in `builtin_skills_root`. |
| `condor/memory/skills.py` | `SkillStore` gains the two-tier resolve: `_iter_skills` yields local ∪ shared (local shadows on slug), `read`/`read_file` resolve local-then-shared, `search` spans both, `_index_lines` marks `(shared)` / `(overrides shared)`. Mutations (`create`/`edit`/`delete`/`write_file`) target the local tier and **error loudly** when the name resolves shared (message per §3). New explicit constructor mode for the shared tier itself (`SkillStore.shared()`) used only by the management path. Traversal guards apply per-tier unchanged. |
| `mcp_servers/condor/tools/skills.py` | `manage_skill` gains `scope: str = "agent"` (`"shared"` valid only from the chat condor — an agent-scoped session passing `scope="shared"` errors). Read/list/search need no parameter: merging is automatic. |
| `condor/agents/prompts.py`, `handlers/agents/_shared.py`, `condor/agents/engine.py` | **No changes** — all three injection sites call `SkillStore(slug).list_index()` and inherit the merged index. |
| `docs/architecture/agent-framework.md` §3 | Rewrite the "no shared tier" paragraphs; document the resolution rule. |
| `assistants/condor/skills/agent_builder/SKILL.md` | Add: before writing domain knowledge into a new agent's body, check the shared tier (`manage_skill(action="list", scope="shared")`) and reference existing skills from the playbook instead of restating them. |
| `tests/test_skill_store.py` | Resolution precedence, shadow marking, shared-edit error, per-reader `routine_ok`, `read_file` tier binding, traversal guards against `agents/_shared/`. |

No engine, web, or frontend changes. Net: one path helper, ~60 lines in
`SkillStore`, one tool parameter.

## 6. Migration — editorial, not scripted

There is one agent to decompose, and the work is content reconciliation
(resolving §2.2's conflicts against the live controller schema), not file
mechanics — a script would add nothing. Do it as the content half of
refactor-01's mm_expert AGENT.md rewrite:

1. Create the three shared skills (§4.2); `git mv` `pmm_config_playbook/` and
   `capital_allocation/` into `agents/_shared/skills/` (companions ride along).
2. Reconcile the parameter guide against
   `manage_controllers(action="describe")` output — the controller source is
   the authority, not either drifted copy.
3. Rewrite AGENT.md per §4.3; slim `pmm_mister_deploy`.
4. **Commit `agents/_shared/` to the repo.** The skills docstring already
   states repo-shipped playbooks are version-controlled; shared knowledge
   doubly so — an untracked shared tier would make §2.2-style drift
   unrecoverable. (Note: `agents/` is currently untracked on this machine —
   the shared tier is the part that must not stay that way.)

`routine_builder` and the other agents need nothing: reading an empty shared
tier is a no-op (empty index contributes no lines).

## 7. Tradeoffs & edge cases

- **Blast radius of a shared edit.** One edit changes every agent's behavior
  at its next read — that is the point (fix the fee table once), but it also
  means a tuning insight valid in one context can degrade another. Mitigation
  is structural: agents cannot write the shared tier (§3), so every shared
  edit passes through the human/chat path. The local-override escape hatch is
  visible in the index, not silent.
- **Agents lose self-refinement over their most-used knowledge.** Today
  mm_expert's prompt says "update skills when you discover a new pattern";
  for shared skills that write now errors. Accepted: the error message routes
  the insight (new local skill, or propose to user), and `learnings.md`
  remains the agent's own append-only channel for exactly this.
- **Read-adherence risk.** Progressive disclosure only works if the playbook
  actually triggers the read. The decomposed AGENT.md makes each read a
  *numbered step* of the flow ("Step 4: execute — read pmm_mister"), the same
  pattern `pmm_mister_deploy` already uses successfully. A model that skips a
  mandatory read mid-flow will produce visibly wrong configs — caught by the
  same review loop (dry runs) that catches prompt bugs today.
- **Stale shadows.** A local skill created before a same-named shared skill
  lands silently wins forever. The `(overrides shared)` index marker keeps it
  visible to both the agent and anyone reading the prompt; `manage_skill
  (action="create")` should additionally warn when the new local name shadows
  an existing shared skill.
- **Shared skills + agent-local routines don't mix.** `routine_ok` is
  per-reader (§3), so a shared skill referencing a routine is broken for most
  readers. Convention (enforced as a create-time warning in the shared scope):
  shared skills describe tool-level flows, not routine calls.
- **Is two-tier lookup a "fallback"?** No — it is a declared resolution order
  over authored data, the same shape as the shutdown policy walk. Nothing
  synthesizes a default on a miss; a missing skill is still a hard miss.
- **Tier is agents-only by design.** If a chat-condor skill and a shared skill
  ever need to merge, that's a fourth-tier question for the day the chat
  itself becomes an agent (framework doc §1) — out of scope.

## 8. What this unlocks (why it's in the series)

- Refactor-01's tradeoff (c) closes: a second MM-family agent is now
  `AGENT.md` (identity + its own playbook) + three index lines — zero copied
  knowledge.
- The "tokenizable unit" framing sharpens: an agent's *edge* is its playbook +
  learnings + track record; its *reference knowledge* is commons. What you'd
  sell/tokenize is the former, which is exactly what AGENT.md now contains.
- Skill quality compounds: one well-maintained `bot_deployment` beats four
  drifting embedded copies, and every improvement (e.g. adding the risk-engine
  loss-cap contract from refactor-02 §4.1) lands everywhere at once.

## 9. Open decisions (recommendations inline)

1. **Move `pmm_config_playbook`/`capital_allocation` as-is, or merge into the
   new `pmm_mister` skill?** *Recommend move as-is first* (a `git mv` — zero
   content risk), then merge editorially in a follow-up pass once the shared
   tier is proven. Four shared skills temporarily is fine.
2. **Should the chat condor read the shared tier?** *Recommend no* (§3) — the
   coordinator delegates domain work; if a specific chat flow needs domain
   knowledge, that's a signal to consult the agent, not to widen the chat
   prompt.
3. **Create-time warning vs hard error for routine references in shared
   skills?** *Recommend warning* — a shared skill referencing a routine that
   *every* current agent happens to ship is legal, just fragile; the per-reader
   `routine_ok` already degrades it gracefully at read time.
