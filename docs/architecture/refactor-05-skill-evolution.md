# Refactor 05 — Skills: one portable format, shared tiers, curated self-improvement

Status: **Phases 1-2 implemented** (2026-07-11). Phase 3 (automatic curation
loop) was implemented, live-validated, then **REMOVED the same day** by user
decision — too complex for the benefit at this scale; the capture side
(learnings) and the primitives it used (`manage_skill patch` with
provenance, `promote_learning`) remain, and skill improvement is
human-directed in chat. Phase 4 skipped. · Builds on:
[refactor-01b](refactor-01b-agent-history-multi-strategy.md) (implemented) and
[refactor-04](refactor-04-shared-skills-tier.md) (tabled → absorbed here as
Phase 2)

**The one-paragraph version:** Condor is used from *inside* host harnesses —
Claude Code, OpenClaw, Hermes — via its MCP server, and all three hosts have
converged on the same open skill standard (agentskills.io SKILL.md). So
Condor skills must be spec-conformant artifacts that install natively in any
host (they currently are not — see §3.2), split into **host-facing** skills
(how to drive Condor's tools; live in the host) and **agent-internal** skills
(the domain agents' brains; live behind the MCP boundary). Skills stay the
*curated product* of experience: agents capture into learnings in-run, a
session-end curation pass promotes cross-episode patterns into skills via
delta edits with provenance and git audit, and a human gates anything that
crosses a sharing boundary. That is what the research supports; autonomous
in-run self-editing is what it warns against.

---

## 1. The problem, from lived evidence

Three observations from implementing refactor-01b/02 and building
mm_expert's second playbook:

1. **Duplicated knowledge rots.** The pmm_mister parameter guide drifted
   between AGENT.md and strategy.md (1000 vs 100 `total_amount_quote`,
   "1–5x" vs "default 20" leverage). Building `grid_range_harvester` *added
   a third copy* of regime knowledge — regime→parameter mappings now live in
   two playbooks AND `skills/pmm_config_playbook/`. Executor-schema
   knowledge is embedded per-playbook and will drift as the API evolves.
   Knowledge needed by several playbooks (or agents) has no home, so it
   gets copied.
2. **The auto-write channel is the wrong shape for procedures.** Learnings
   work (live-tested: `[funding_snapshot]`-prefixed provenance, dedup,
   cap 20) — but they are flat one-line facts. Nothing promotes "the agent
   learned a procedure works" into a playbook or skill. Ticks are told
   skills are read-only; experience never flows back into them.
3. **The deployment context is hosts-first.** Users run Condor's MCP inside
   Claude Code, OpenClaw, and Hermes. Each host has its own skills system
   on the same standard; Condor's chat-level skills (agent_builder, …)
   currently reach a host only through `manage_skill` tool calls — invisible
   to the host's native skill index, and not conformant enough to install
   there.

## 2. Research

### 2.1 The standard: agentskills.io SKILL.md

Published by Anthropic late 2025, now an open spec adopted by Claude Code,
OpenClaw, Hermes (and shipping-skill orgs: OpenAI/HF/NVIDIA publish tap
repos). Verified against the spec text:

- A skill = a directory with `SKILL.md` (YAML frontmatter + markdown body);
  optional `scripts/`, `references/`, `assets/`, any other files.
- **`name` (required):** 1–64 chars, lowercase alphanumerics and hyphens
  only, no leading/trailing/consecutive hyphens, **must match the parent
  directory name**. (Underscores are invalid.)
- **`description` (required):** 1–1024 chars, must say *what it does and
  when to use it* — the description IS the routing trigger; hosts preload
  only name+description (~100 tokens) at startup.
- Optional: `license`; `compatibility` (≤500 chars — environment
  requirements, e.g. a required MCP server); `metadata` (**string keys to
  string values** — the vendor-extension escape hatch; use uniquely-prefixed
  keys); `allowed-tools` (experimental).
- Progressive disclosure contract: metadata always loaded → body (<5k
  tokens, <500 lines recommended) on activation → bundled files on demand.
- A reference validator exists: `skills-ref validate ./my-skill`.

### 2.2 The harnesses Condor runs inside

**Claude Code** — scopes: personal `~/.claude/skills/`, project
`.claude/skills/` (checked into the repo; discovered per-directory in
monorepos), plugin skills (namespaced, via marketplaces); precedence on name
clash; skills double as `/slash-commands`; directories are file-watched.
Anthropic's implementation adds `allowed-tools`, `disable-model-invocation`,
`context: fork`. Self-improvement stance: human-in-the-loop — a
`skill-creator` skill scaffolds authoring; the engineering guidance is "ask
Claude to capture its successful approaches into a skill". No autonomous
background editing.

**OpenClaw** — discovers any `SKILL.md` under a configured workspace
`skills/` root **up to 6 levels deep** (folder path is organizational only);
`openclaw skills install` (ClawHub registry) lands in the workspace `skills/`
dir. Two implementation constraints beyond the spec: its embedded parser
supports **single-line frontmatter values only** (`metadata` as a
single-line JSON object), and it gates loading mechanically via
`metadata.openclaw` `requires` entries (env vars that must exist, binaries
that must be installed) so ungated skills stay dormant. Humans author;
agents consume.

**Hermes (Nous Research `hermes-agent`)** — same spec; three-tier
progressive disclosure identical in shape to Condor's (index → SKILL.md →
reference file). Sharing is Homebrew-style **taps** (`hermes skills tap add
owner/repo`, expecting `skills/<name>/SKILL.md` layout), plus skills.sh,
`.well-known/skills/` endpoints, and community hubs; trust tiers per source.
Distinctive: the **agent edits its own skills in-run** (`skill_manage`
create/patch/edit/delete, prompted to save after any 5+-tool-call success)
plus a human-triggered `/learn`. Its guardrails are opt-in and leaky in
practice: write-approval staging **off by default**, scanning of the agent's
own creations **off by default**, documented scanner bypasses (obfuscated
exfiltration scoring zero findings; skill descriptions injected into the
system prompt unscanned), no git, no outcome measurement. Hermes also frames
memory as episodic (SQLite) / semantic (MEMORY.md) / procedural (skills) —
exactly Condor's journal / learnings / skills triple, independent
confirmation of the decomposition.

**The broader field** (for completeness): Codex AGENTS.md (hierarchical
markdown, git-shared, no triggering); OpenHands skills (project > user > org
tiers + *keyword-triggered* injection); Cursor rules (agent-generated but
human-triggered, git-reviewed) and Windsurf memories (auto-captured, UI
review panel); Letta memory *blocks* attached to N agents with a
**sleep-time agent** doing consolidation while the primary agent is stripped
of memory-edit tools; LangMem — the only mainstream framework where outcome
*scores* are first-class input to procedure updates; AG2 Teachability
(per-agent vector memos, no sharing, no outcome check).

### 2.3 What the literature says about self-improvement

- **Voyager** (2023): the skill *library* is what makes learning cumulative
  (ablating it plateaus progress); skills enter only after **verified**
  execution success; retrieval by description embedding. Limits: no
  pruning/dedup, verifier hallucinations admit false skills.
- **ACE** (2025): full-rewrite self-editing fails via **brevity bias**
  (rewrites compress toward generic mush, losing the domain detail that
  carries performance) and **context collapse** (lossy regeneration
  compounds; a documented 18k→120-token collapse in one rewrite). Fix:
  itemized store, **delta operations only**, a Reflector (judge) separate
  from the Curator (writer), semantic dedup, helpful/harmful counters.
- **Reflexion**: helps within-task retries; memory confabulation documented
  (reflections reinforcing false beliefs); nothing accumulates cross-task.
- **ExpeL / AWM / CLIN**: distill only **cross-episode** patterns;
  ADD/EDIT/UPVOTE/DOWNVOTE with delete-at-zero (outcome-weighted
  retention); constrained schemas beat free text.
- **Memory-management & poisoning studies** (2025-26): agents
  *experience-follow* — one flawed stored episode is replayed and compounds;
  naive add-everything memory makes long-horizon performance **decline**;
  a single poisoned "success example" corrupts future tasks until purged.
  Provenance and purgeability are security features, not bookkeeping.

**Consensus, across field and literature:** package skills as files with
git; share via tiers/precedence or repo distribution; separate *doing* from
*learning* (a background/curation role holds the pen, never the in-run
agent); write-gate on verified cross-episode patterns; delta edits with
provenance; retire what stops helping. Nobody ships autonomous free-form
self-editing with the guardrails on by default — and the one framework that
comes closest (Hermes) demonstrates why that ordering is wrong.

## 3. Outcome — the design

### 3.1 Architecture: two skill estates, three tiers, one format

```
HOST (Claude Code / OpenClaw / Hermes / Condor chat)
│  host's own skill index — native discovery, /slash invocation
│
├─ HOST-FACING SKILLS            assistants/condor/skills/<name>/SKILL.md
│    "how to drive Condor via MCP": agent-builder, log-analyzer, …
│    strictly spec-conformant; installed IN the host (tap / .claude/skills /
│    workspace discovery); gated on the Condor MCP server being connected
│
╌╌ MCP boundary (mcp_servers/condor) ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
│
└─ AGENT-INTERNAL SKILLS         consumed only by Condor's own runs
     agents/{slug}/skills/           local tier   (agent-writable, curated)
     agents/_shared/skills/          shared tier  (chat/human-writable only)
     assistants/condor/skills/       builtin tier (chat's own; host-facing set)
     resolution: local > shared > builtin — most specific wins
```

- The **host never sees agent-internal skills** (it must not "helpfully"
  execute mm_expert's deploy playbook itself); Condor's tick/consult/
  delegation runs never depend on host skills.
- Both estates use the **same spec-shaped format**, so promotion
  (internal → shared → host-facing) is a file move plus a human decision —
  never a conversion.
- The memory triple stays: journal = episodic, learnings = semantic
  capture, skills = procedural product.

### 3.2 The format contract (and our current gaps)

Target frontmatter — spec-strict, OpenClaw-parser-safe (single-line values,
flat string-valued metadata with `condor-` prefixed keys):

```markdown
---
name: pmm-mister-deploy
description: End-to-end pmm_mister bot deployment — regime analysis, profile selection, config creation, deploy. Use when asked to set up or launch a market making bot on a pair.
compatibility: Requires the Condor MCP server (mcp__condor__*) connected
metadata: {"condor-source": "agent:market_making_expert", "condor-created": "2026-07-02", "condor-references-routine": "market_analyzer", "condor-updated-by": "", "condor-changelog": ""}
---
```

Conformance gaps this fixes (audited against our ~10 skills):

| Gap | Today | Target |
|---|---|---|
| Names use `_` (spec: hyphens only, must match dir name) | `agent_builder`, `pmm_mister_deploy` | rename dirs+names to `agent-builder`, `pmm-mister-deploy`, …; `_slugify` for skills switches to hyphens |
| `when_to_use` as a separate field | routing signal lives outside `description` | fold into `description` ("… Use when …"), per spec — one field, no drift between two routing texts |
| Multiline folded frontmatter values | YAML line-wrapping | single-line values (OpenClaw parser) |
| Non-spec top-level fields (`source`, `created`, `references_routine`) | top level | `metadata` string keys: `condor-source`, `condor-created`, `condor-references-routine` |
| No environment gating | a host would surface skills with no Condor MCP connected | `compatibility` (spec) + `metadata.openclaw` requires-gating where useful |
| No validation | format enforced only by our writer | `skills-ref validate`-equivalent checks in the test suite |

### 3.3 Self-improvement: capture → curate → promote

**Principle: learnings are the capture channel, skills are the curated
product, and the pen is held by a curation pass — never by the in-run
agent.** Concretely:

1. **Capture (in-run, unchanged):** ticks/delegations write learnings
   (facts, provenance-prefixed, deduped, capped) and leave
   journals/transcripts. The tick prompt's "skills are read-only" stays.
2. **Curate (session-end, the new piece):** on a trigger — a tick session
   stopping, every N sessions, or an explicit chat request ("turn how we
   just did X into a skill") — run a **curation pass**: a delegation to the
   agent (or a shared `skill_curator` flow) under the AUTO policy
   (serverless; no risk gate needed). Input: learnings + last N session
   journals/transcripts + the skills index. Mandate:
   - **delta edits only** via a new `patch` action (old_string→new_string,
     Edit-tool shape) — full-body rewrites are reserved for humans (ACE:
     context collapse is a one-bad-rewrite failure);
   - only patterns evidenced in **≥2 sessions** (single-episode reflexes
     stay in learnings — the Reflexion/AWM overfit lesson);
   - semantic dedup against existing skill content before adding;
   - consumed learnings are marked promoted, freeing the cap.
3. **Provenance + audit on every edit:** `condor-updated-by: {session_id}`
   and a one-line `condor-changelog` entry; the pass ends with a **git
   commit scoped to the skill paths** (skills are already tracked) —
   audit trail, review surface, rollback, and poisoning purge in one
   mechanism, on by construction (the Hermes lesson: guardrails that are
   configuration default to off).
4. **Human gates at every sharing boundary:** curation writes the agent's
   **local tier only**. Promotion local → `_shared` (affects every agent)
   or → host-facing (affects the *host*) happens only via the chat with
   explicit user confirmation — the same posture as delegation risk caps:
   what crosses an agent boundary gets said out loud to a human.
5. **Outcome-weighted retention (later, Phase 4):** sessions already carry
   track records; stamp the skills-index git sha into session meta.yml and
   per-skill-version performance falls out of the existing rollup —
   ACE-style helpful/harmful counters, and the curator's mandate extends to
   retiring sections whose introduction preceded degraded sessions.

**Deliberate non-goals:** no in-tick skill editing; no autonomous
shared/host-tier writes; no full-rewrite "improve this skill" prompts; no
vector retrieval until an index outgrows prompt injection (~50+ skills per
scope); no Condor-side marketplace (that is host business — we are a good
citizen of *their* registries, not a competitor to them).

## 4. How it fits each harness

### 4.0 Deployment assumption: the host opens the Condor repo as its workspace

Users run Claude Code, OpenClaw, and Hermes *in the condor directory*. This
assumption does two good things and one honest thing:

**It collapses distribution to one git-tracked directory.** All three hosts
can be served by a single canonical home at the **repo root: `skills/`**:
- **OpenClaw** scans `<workspace>/skills` by default — automatically
  discovered, and workspace skills take *top precedence* over user-global
  duplicates (verified: it scans only designated roots — `<ws>/skills`,
  `<ws>/.agents/skills`, `~/.agents/skills`, managed/bundled — never the
  whole workspace).
- **Hermes** taps expect exactly `skills/<name>/SKILL.md` from the repo
  root; a local checkout also works via `skills.external_dirs`.
- **Claude Code** discovers `.claude/skills/` — per-skill symlinks into
  `../skills/`.
- **Condor's chat** repoints its builtin root to the same directory.
One source of truth; `git pull` updates every host at once; no stale copies
drifting in `~/.claude` or `~/.hermes`. (`assistants/condor/skills/` content
moves to `skills/` in Phase 1.)

**Index isolation is structural.** None of the hosts' skill roots include
`agents/{slug}/skills/` or `agents/_shared/skills/`, so agent-internal
skills stay out of every host's index by construction even with the host
sitting inside the repo. The estate split survives repo-as-workspace intact.

**File isolation does not exist — say so and design for it.** A host opened
in the repo is a coding agent with the whole tree as its workspace: it can
read or edit `agents/*/skills/`, learnings, session state as plain files,
MCP boundary or not. The boundary isolates Condor's *runtime*, not the
filesystem. What actually protects the internal estate here: (a) everything
is git-tracked, so any host-side mutation is a visible diff, never silent;
(b) every host-facing skill carries an explicit rule — **"Condor's
`agents/` and `assistants/` trees are runtime state; operate Condor ONLY
via `mcp__condor__*` tools, never by editing its files"** — which also
prevents the subtler failure of a host "helpfully" editing a strategy.md
directly instead of going through `manage_trading_agent`; (c) host and
Condor run in the same trust domain (the same user), so this is drift
prevention, not an adversarial boundary.

### 4.1 Condor's own chat (Telegram / web)

Unchanged consumer, same files: `SkillStore` keeps injecting the
name+description index into chat context and serving bodies via
`manage_skill(action="read")`. After Phase 1 the index line is built from
`description` alone (the folded trigger), and internal skills resolve
local > shared > builtin. The chat remains the **management plane**: it is
where shared-tier and host-facing skills get authored/approved, and where
promotion confirmations happen.

### 4.2 Claude Code

- **Distribution:** the repo ships `.claude/skills/` as per-skill symlinks
  into the canonical root `skills/` (one source of truth; fall back to a
  sync script + CI freshness check if symlink handling proves flaky —
  verify at implementation). Anyone opening the Condor repo in Claude Code
  gets `agent-builder` etc. natively, as skills and as `/slash-commands`.
- **Users outside the repo** copy the skill dirs to `~/.claude/skills/`
  (or install a future Condor plugin) — secondary path; repo-as-workspace
  is the primary deployment (§4.0).
- **Runtime:** the skill instructs driving `mcp__condor__*` tools;
  `compatibility` notes the MCP requirement (Claude Code has no mechanical
  gating; the description tells the model when it applies).
- **Self-improvement interplay:** none crosses the boundary — Claude Code's
  skill-creator may author *host-level* skills; Condor's curation loop
  never writes into `.claude/`.

### 4.3 OpenClaw

- **Distribution:** opening the Condor repo as the OpenClaw workspace is
  sufficient — `<workspace>/skills` is scanned by default (recursively, ≤6
  levels) and takes top precedence over user-global/bundled duplicates.
  ClawHub publication remains the path for users not working in the repo.
- **Conformance specifics honored:** single-line frontmatter values;
  `metadata` as single-line JSON; `metadata.openclaw` requires-gating so
  Condor skills stay **dormant** in workspaces where the Condor MCP server
  isn't configured — mechanical, not advisory.
- **Self-improvement interplay:** OpenClaw agents consume, humans author —
  matches our model; nothing to reconcile.

### 4.4 Hermes

- **Distribution:** the canonical repo-root `skills/` IS the tap layout —
  `hermes skills tap add <condor-repo>`, or for a local checkout
  `skills.external_dirs: ["<condor>/skills"]`; later, the Condor web server
  can expose `.well-known/skills/`. The community trust tier applies;
  Hermes's Skills Guard scans at install — spec-clean, script-free markdown
  skills pass trivially.
- **Runtime:** Hermes's own three-tier disclosure loads them exactly like
  its native skills; slash invocation (`/agent-builder …`) works.
- **Self-improvement interplay — the one real hazard:** Hermes agents edit
  their own installed skills in-run (`skill_manage`), which could mutate an
  installed Condor skill inside `~/.hermes/skills/`. That copy is the
  *host's* — drift there is contained (tap `update` restores; our upstream
  stays canonical in git), but the Hermes-user docs should recommend
  enabling `skills.write_approval` and treating Condor skills as
  tap-managed (update via tap, don't self-edit). Condor's internal estate
  is out of Hermes's skill index by construction; under repo-as-workspace
  it remains *file-reachable* like any workspace file — §4.0's mitigations
  (git visibility + the "operate via MCP tools only" rule in every
  host-facing skill) are what cover that residual.

### 4.5 Any other MCP host

The generic story: spec-conformant SKILL.md + `compatibility` line +
description-based routing works in any agentskills.io host; hosts without a
skills system still get the `manage_skill`-mediated behavior through
Condor's MCP instructions block, unchanged.

## 5. Implementation plan

Each phase lands green and independently.

### Phase 1 — Format conformance + host distribution ✅ (implemented 2026-07-11)

1. `condor/memory/skills.py`: hyphen slugs for skills (skills-only — the
   memory store keeps its underscore slugs); a **skill-specific frontmatter
   renderer** — the shared `store._render` emits block-style YAML
   (`yaml.dump(default_flow_style=False)`), which violates OpenClaw's
   single-line constraint, so skills get their own writer emitting
   single-line values and `metadata` as flow-style/JSON; `create`/`edit`
   lose the `when_to_use` param (description carries it); `list_index` and
   `read` handle the new shape only (`read` keeps resolving
   `condor-references-routine` for the routine_ok check).
2. `mcp_servers/condor/server.py` + `tools/skills.py`: `manage_skill`
   signature/docstring updates.
3. Migration script (`scripts/migrate_skill_frontmatter.py`): rename
   dirs/names to hyphens; fold `when_to_use` into `description`
   (mechanical merge; **warn, don't truncate** when the result is >1024
   chars or reads badly — flagged for manual trim); move
   `source`/`created`/`references_routine` into `metadata`; single-line
   everything. Then a **reference audit** (the refactor-01b lesson): grep
   every AGENT.md / strategy.md / SKILL.md / prompt builder for old skill
   names (`routine_cookbook` → `routine-cookbook`, …) and update — prose
   prompts are invisible to the type checker. Audited blast radius: live
   references in mm_expert AGENT.md (2), routine_builder AGENT.md (3),
   condor AGENT.md (1) plus comment-level code mentions; **historical
   artifacts (dry_runs snapshots, session transcripts) are records and are
   NOT rewritten**.
4. Canonical host-facing home: move `assistants/condor/skills/*` to
   repo-root `skills/` (serves OpenClaw workspace discovery + Hermes taps
   natively); repoint `builtin_skills_root(None)`; add `.claude/skills/`
   per-skill symlinks. Every host-facing skill body gains the §4.0 rule:
   operate Condor only via `mcp__condor__*` tools, never by editing its
   files.
5. Tests: rewrite `test_skill_store.py` for the new shape; add a
   conformance test enforcing the spec rules (name pattern, dir match,
   description bounds, single-line values) on every SKILL.md in the repo.
6. Acceptance: install and route the host-facing set in Claude Code
   (`.claude/skills/`), OpenClaw (workspace discovery + gating), and
   Hermes (tap). Claude Code is verifiable immediately in this repo; the
   OpenClaw/Hermes checks run on whichever machine has those harnesses
   installed — recorded as a user-runnable checklist in the PR.

### Phase 2 — Shared tier + dedupe (refactor-04, updated) ✅ (implemented 2026-07-11)

1. `SkillStore` resolution local > shared > builtin; `_shared` writable
   only via chat `scope="shared"`; loud error on agent writes to shared.
2. Content moves that kill the observed drift:
   `_shared/skills/executor-mechanics/` (grid/position/order schemas + the
   limit_price/keep_position risk model — used by both mm_expert playbooks
   and revival_trader); mm_expert-local `regime-playbook/` (the single
   regime→posture mapping, per-playbook parameter tables as reference
   files); reconcile the pmm param drift into `pmm-config-playbook` while
   at it. Playbooks slim to decision logic + "read skill X before Y".
3. Web/MCP: shared-tier listing in the chat's skill management.

### Phase 3 — The curation loop ✅ (implemented 2026-07-11)

1. `SkillStore.patch(name, old, new)` + `manage_skill(action="patch")`;
   provenance fields updated on every write.
2. `skill_curator` flow: curator prompt (mandates from §3.3), triggered by
   session end (an engine `_finalize_meta` hook firing a delegation),
   every-N-sessions, or chat request; writes local tier only.
3. Git commit step scoped to `agents/{slug}/skills/**`, message
   `skills({slug}): <changelog line>`.
4. Promotion flow in chat: curator proposals surface as a notification;
   the `scope="shared"` copy happens only on user confirmation.
5. Learnings: mark-promoted section in learnings.md.

### Phase 4 — Outcome-weighted retention (optional, after Phase 3 runs)

Session meta.yml gains the skills git sha; the perf rollup grouped by
skill-version gives per-edit A/B; the curator gains a retire mandate. Build
only once curation is actually running — measurement before there is a loop
is ceremony.

## 6. Open decisions

1. **Symlinks vs sync for `.claude/skills/`** — recommend symlinks into
   the canonical `skills/` root; verify Claude Code + git behavior, fall
   back to a checked-in copy + CI freshness check.
2. **Hyphen-rename blast radius** — skill names appear in agent prose and
   both mm_expert playbooks; the audit covers repo files, but users' chat
   habits ("read routine_cookbook") break once. Loud not-found errors plus
   the index make this self-healing; accepted.
3. **Auto-commit posture** — curation commits touch only
   `agents/{slug}/skills/**`; if that still feels intrusive, the fallback
   is staging the diff and having the chat ask — decide at Phase 3.
4. **`allowed-tools` on host-facing skills** — experimental in the spec;
   revisit when host support stabilizes.
5. **Chat-created skills are host-visible by definition** *(resolved)*:
   after the move, the chat's `manage_skill(action="create")` writes into
   repo-root `skills/` — i.e. every host's index. Accepted: chat authoring
   is user-supervised and git-visible, and the rule is placement-based —
   knowledge meant for one agent belongs in that agent's local tier (the
   chat authors there via `agent_slug`); only genuinely host-relevant
   playbooks belong in `skills/`. The `manage_skill` docstring states this
   so the chat model places skills deliberately.

## 7. Sources

Spec: [agentskills.io/specification](https://agentskills.io/specification) ·
[skills-ref validator](https://github.com/agentskills/agentskills/tree/main/skills-ref)
· Hosts: [Claude Code skills](https://code.claude.com/docs/en/skills),
[Anthropic engineering on Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills),
[OpenClaw skills](https://docs.openclaw.ai/tools/skills) /
[skill format](https://docs.openclaw.ai/clawhub/skill-format),
[Hermes skills docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) /
[repo](https://github.com/NousResearch/hermes-agent) ·
Field: [Letta sleep-time agents](https://docs.letta.com/guides/agents/architectures/sleeptime/),
[LangMem](https://www.langchain.com/blog/langmem-sdk-launch),
[OpenHands skills](https://docs.openhands.dev/overview/skills) ·
Literature: [Voyager](https://arxiv.org/abs/2305.16291),
[ACE](https://arxiv.org/abs/2510.04618),
[Reflexion](https://arxiv.org/abs/2303.11366),
[ExpeL](https://arxiv.org/abs/2308.10144),
[AWM](https://arxiv.org/abs/2409.07429),
[CLIN](https://arxiv.org/abs/2310.10134),
[memory management impacts](https://arxiv.org/abs/2505.16067),
[MemoryGraft](https://arxiv.org/pdf/2512.16962).
