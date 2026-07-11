# Refactor 05 — Skills: shared tiers + curated self-improvement

Status: **proposed** (2026-07-11) · Builds on:
[refactor-01b](refactor-01b-agent-history-multi-strategy.md) (implemented) and
[refactor-04](refactor-04-shared-skills-tier.md) (tabled → absorbed here as
Phase 1)

## 1. What this session taught us

Three observations from implementing 01b/02 and building the second
mm_expert playbook drive this design:

1. **Duplicated knowledge rots.** The pmm_mister parameter guide drifted
   between AGENT.md and strategy.md (1000 vs 100 `total_amount_quote`,
   "1–5x" vs "default 20" leverage). Then, building `grid_range_harvester`,
   we *added* a third copy of regime knowledge — regime→parameter mappings
   now live in `pmm_mister_operator/strategy.md`, `grid_range_harvester/
   strategy.md`, AND `skills/pmm_config_playbook/`. Executor-schema knowledge
   is embedded per-playbook and will drift as the API evolves. Knowledge that
   several playbooks (or agents) need has no home, so it gets copied.
2. **The write channel exists but is the wrong shape for procedures.**
   Learnings work (live-tested: `[funding_snapshot]`-prefixed provenance,
   dedup, cap 20) — but they are flat one-line facts. There is no path from
   "the agent learned a procedure works" to "the playbook/skill now says so."
   Ticks are told skills are read-only; nothing ever promotes experience into
   them.
3. **We already have the substrate most frameworks lack.** Skills are
   file-based, git-tracked, progressively disclosed (index → SKILL.md →
   companion files), and every run now leaves an attributed session with
   meta.yml + journal/transcript. That is precisely the packaging +
   provenance + outcome-measurement base the survey below says matters.

## 2. What the field does (survey summary)

Full briefs live in the research transcripts; the load-bearing findings:

**Packaging & sharing — industry consensus is files + git + precedence
tiers.** Anthropic Agent Skills (now an open standard): SKILL.md +
progressive disclosure, personal/project/plugin scopes, precedence on name
clash, shared by checking into the repo. Codex AGENTS.md: hierarchical
markdown, deeper wins. OpenHands skills: project > user > org tiers, plus
*keyword-triggered* injection. Cursor/Windsurf rules: same file+git pattern.
Letta is the one different model: shared memory *blocks* attached to N
agents, writes visible to all. → Condor's SKILL.md store is already the
consensus shape; what's missing is the shared tier and precedence.

**Self-improvement — nobody ships autonomous free-form self-editing; every
production system separates doing from learning:**
- Letta *sleep-time agents*: the primary agent is stripped of memory-edit
  tools; a background agent that shares its memory does the consolidation.
- Cursor: agent-generated rules, but human-triggered (`/Generate Cursor
  Rules`) and git-reviewed. Windsurf auto-captures memories but exposes a
  review UI. Anthropic: "ask Claude to capture its successful approaches
  into a skill" — human in the loop, `skill-creator` scaffolds it.
- LangMem is the only mainstream framework where *outcome scores* are a
  first-class input to prompt/procedure updates.

**Why free self-editing fails (research is unambiguous):**
- **ACE** (Stanford 2025): full-rewrite self-editing suffers *brevity bias*
  (rewrites compress toward generic mush, losing the domain detail that
  carries performance) and *context collapse* (lossy regeneration compounds;
  documented 18k→120-token collapse in one step). Fix: itemized store,
  **delta operations only** (add/update/remove specific bullets), a
  Reflector (judge) separate from the Curator (writer), semantic dedup,
  helpful/harmful counters per item.
- **Memory-management studies** (ACL 2026): agents *experience-follow* —
  one flawed stored episode gets replayed and compounds; naive
  add-everything memory makes long-horizon performance *decline*.
  Quality-gated writes + deletion of stale/erroneous entries fix it.
  MemoryGraft: a single poisoned "success example" corrupts future tasks
  until purged — provenance and purgeability are security features.
- **What works:** Voyager (skills enter the library only after *verified*
  success; retrieval by description embedding; library is what makes
  learning cumulative), ExpeL (ADD/EDIT/UPVOTE/DOWNVOTE with delete-at-zero
  — outcome-weighted retention), AWM (distill *cross-episode* workflows,
  not single-episode reflexes), CLIN (constrained schemas beat free text).

### 2.1 Hermes agent (Nous Research) — the counterexample worth studying

`hermes-agent` is the one production framework that ships agent-authored
skills as a headline feature ("procedural memory"), so it deserves its own
read. Same SKILL.md open standard (agentskills.io) and the same three-tier
progressive disclosure we already have. Its memory framing maps 1:1 onto
ours: episodic (SQLite) / semantic (MEMORY.md) / procedural (skills) ≈ our
journal / learnings / skills — independent confirmation that the triple is
the right decomposition.

**How it self-improves:** an in-run `skill_manage` tool (create /
**patch** (old_string→new_string) / edit / delete) with prompt heuristics —
"save a skill after a complex 5+-tool-call task or after escaping a dead
end" — plus a human-triggered `/learn <dir|URL|"how I just did X">` command
that drafts a standards-compliant skill from material the agent gathers.

**What its guardrails actually look like in practice:** a staging/approval
flow exists (`skills.write_approval` → pending dir → `/skills
approve|reject`) but is **off by default**; an LLM security scanner for
installed skills exists but scanning the agent's *own* creations is also
off by default, and its issue tracker documents real bypasses (obfuscated
exfiltration scoring zero findings; skill descriptions injected into the
system prompt unscanned). No git integration, no outcome measurement.

**The real conclusion is not to copy any of this.** Condor's deployment
context is the opposite of Hermes's: **users run Condor's MCP server
*inside* a host agent — Hermes, Claude Code, whatever speaks MCP.** Hermes
is a host harness competing on host features (marketplaces, /learn, in-run
self-editing); Condor is the domain layer behind the MCP boundary. What
matters is that Condor's skills are **compatible** with that world — a
Hermes user should be able to install Condor's skills natively — not that
Condor grows its own copies of host features. §3.0 makes this the
organizing constraint. (Two Hermes details do inform our internals either
way: its `patch` old_string→new_string op is the right mechanical shape for
our deltas-only mandate, and its off-by-default guardrails with documented
bypasses are the cautionary case for keeping ours on by construction.)

## 3. Recommendation

Three phases, each independently shippable. Two principles throughout:
**skills conform to the agentskills.io standard so they run in any host**,
and **learnings are the capture channel, skills are the curated product,
and the pen is held by a curation pass — never by the in-run agent.**

### Phase 0 — The compatibility contract (host/domain split + spec alignment)

Condor is used from inside host agents via its MCP server. That splits the
skill estate into two kinds with different consumers, and the boundary IS
the design:

- **Host-facing skills** (`assistants/condor/skills/`: agent_builder,
  log_analyzer, …) teach *how to drive Condor's MCP tools*. Their natural
  home is the **host's own skills system** — Hermes, OpenClaw, Claude Code,
  anything speaking the agentskills.io standard — discovered by the host's
  index, not tunneled through `manage_skill`. They must therefore be
  strictly spec-conformant. Per-host reality check (all verified against
  current docs):
  - **Claude Code**: project scope is `.claude/skills/` — ship the
    host-facing set there (or symlink it to `assistants/condor/skills/`),
    and anyone running Claude Code in the Condor repo gets them natively.
  - **OpenClaw**: discovers any `SKILL.md` under a configured workspace
    `skills/` root up to 6 levels deep, so a checked-out Condor repo is
    discoverable as-is; `clawhub` install lands in the workspace `skills/`
    dir. Two conformance constraints matter: its embedded parser wants
    **single-line frontmatter values** (`metadata` as single-line JSON),
    and it gates loading via `metadata.openclaw.requires` (env vars,
    binaries) — our skills should declare their Condor-MCP dependency
    there so they stay dormant in hosts without the server.
  - **Hermes**: tap-compatible layout already (`skills/<name>/SKILL.md`);
    `hermes skills tap add <condor repo>` works once frontmatter conforms;
    `.well-known/skills/` from the web server is a later option.
  Condor's own Telegram/web chat keeps loading the same files exactly as
  today — same skills, N consumers.
- **Agent-internal skills** (`agents/{slug}/skills/`, and Phase-1's
  `_shared` tier) are the domain agents' brains, consumed by Condor's own
  tick/consult/delegation runs behind the MCP boundary. They are never
  exposed to the host index (the host shouldn't "helpfully" follow
  mm_expert's deploy playbook itself), but they use the **same spec-shaped
  format** — so promotion (internal → host-facing), export, and tooling are
  file moves, not conversions.

**Spec alignment (one-time migration, no dual-format reader):** `name` and
`description` stay top-level per the spec, with the routing trigger folded
into `description` (the spec's description IS the when-to-use signal, ≤160
chars and single-line for OpenClaw's parser); Condor-specific fields —
`when_to_use`, `references_routine`, `source`, `created`, and Phase-2's
`updated_by`/`changelog` — move under the spec's vendor namespace as
`metadata.condor` (kept single-line-JSON-safe; the same mechanism Hermes
and OpenClaw use for `metadata.hermes`/`metadata.openclaw`). Host-facing
skills additionally declare their runtime dependency on the Condor MCP
server via the host gating fields where supported. `SkillStore`
reads/writes the new shape only; the migration script rewrites the
existing ~10 skills in place. Acceptance test for the phase: the
host-facing set installs and routes correctly in Claude Code
(`.claude/skills/`), OpenClaw (workspace discovery), and Hermes (tap).

**Self-improvement stays domain-side.** The curation loop (Phase 2) edits
agent-internal skills only. Host-level skill learning (Hermes `/learn`,
`skill_manage`, Claude skill-creator) is the host's business — Condor
neither depends on it nor fights it. The one interaction point: curation
may propose promoting a domain skill to host-facing; that promotion is the
human-gated boundary crossing.

### Phase 1 — Shared tier + dedupe (un-table refactor-04, mostly as written)

- `agents/_shared/skills/` readable by every domain agent; resolution
  **local > shared > builtin** (name clash: most specific wins — the
  industry precedence pattern). `_iter_agent_dirs` already skips `_` dirs;
  refactor-04 §4 has the SkillStore changes.
- Chat manages the shared tier via `manage_skill(scope="shared")`; domain
  agents get a **loud error** on writes to shared (read-only from below).
- **Dedupe the drift we found**: move regime→parameter knowledge and
  executor mechanics into skills; playbooks *reference* skills instead of
  embedding copies:
  - `_shared/skills/executor_mechanics/` — grid/position/order executor
    schemas + the limit_price/keep_position risk model (used by mm_expert
    playbooks AND revival_trader),
  - mm_expert local `regime_playbook/` — the single regime→posture mapping,
    with per-playbook parameter tables as companion files,
  - reconcile the pmm param drift into `pmm_config_playbook` while at it
    (the outstanding editorial item).
  Playbooks keep only their decision logic + a "read skill X before Y" line.
  This shrinks tick prompts too (playbook bodies are injected every tick;
  skills load on demand — progressive disclosure we already have).

### Phase 2 — The curation loop (auto-improvement, done the ACE way)

Add a **skill curation pass** — Condor-native sleep-time compute, built from
primitives we already have (delegation + sessions + git):

1. **Capture stays as-is**: ticks/delegations write learnings (facts) and
   journals/transcripts (episodes). No in-run skill editing — the tick
   prompt's "skills are read-only" line stays.
2. **Curate on a trigger** — session end (a stopped tick session), every N
   sessions, or an explicit chat request ("turn how we just did X into a
   skill" — the user is the trigger, the agent does the authoring).
   Mechanically both are a delegation to the agent itself (or a shared
   `skill_curator` flow) with an AUTO policy — serverless, so no risk gate
   needed:
   - Input: the agent's learnings.md + the last N session journals/
     transcripts + current skills index.
   - Mandate: **delta edits only** — append/refine a specific section, merge
     a duplicate, retire a stale bullet; never rewrite a skill wholesale;
     only distill patterns seen in **≥2 sessions** (single-episode reflexes
     stay in learnings). Consumed learnings are marked (moved to a
     "promoted" section) so the 20-cap stops evicting valuable ones.
   - Output: `manage_skill` calls on the agent's **local** tier only, via a
     new `patch` action (old_string→new_string, mirroring the Edit-tool
     shape) — deltas-only enforced mechanically, with `edit`/full-body
     rewrites reserved for the human side.
3. **Provenance on every edit**: skill frontmatter gains
   `updated_by: {session_id}` + a one-line `changelog:` entry (mirrors the
   `[strategy]` learning prefixes), and the curation pass ends with a **git
   commit** of the skill diff — free audit trail, review surface, and
   rollback (the packaging consensus AND the poisoning mitigation in one
   mechanism).
4. **Human gate at the tier boundary**: the curator may *propose* promoting
   a local skill to `_shared` (it affects every agent); promotion happens
   only via the chat with user confirmation. Local self-edits are
   auto-applied but git-visible; shared edits are human-approved. This is
   the Letta/Cursor guardrail split, mapped onto our tiers.

### Phase 3 (optional, later) — Outcome-weighted retention

Our sessions already carry track records; close the loop nobody ships:
- Curation commits give each skill a version (git sha); tick sessions record
  the skill index sha in meta.yml.
- Perf rollups per skill-version become an A/B read, ACE-style
  helpful/harmful counters per skill; the curator's mandate extends to
  *retiring* sections whose introduction preceded degraded sessions.
- Do this only if Phase 2 curation actually runs regularly — measurement
  before there's a loop to measure is ceremony.

## 4. What we deliberately do NOT do

- **No in-tick skill editing.** Wrong moment (single-sample, noisy, and the
  tick has a 300s budget); the literature's overfit/poisoning failure modes
  all start here.
- **No autonomous shared-tier writes.** Blast radius is every agent;
  numbers/procedures crossing that boundary get said out loud to a human —
  same posture as delegation risk caps.
- **No full-rewrite "improve this skill" prompts.** ACE's context collapse
  is a one-bad-rewrite failure; deltas only.
- **No vector store yet.** The index-injection + when_to_use routing works
  at current library size (~10 skills); Voyager-style embedding retrieval
  becomes worthwhile only when indexes outgrow the prompt. Revisit at ~50+
  skills per scope.

## 5. Sequencing & cost

Phase 0 is a frontmatter migration + docs (the layout is already
tap-compatible). Phase 1 is small (SkillStore tier resolution + scope param
+ content moves — refactor-04 §4-6 already specifies it) and pays
immediately by deleting the three-way regime duplication. Phase 2 is one new flow (curator prompt +
trigger + git commit step) on existing primitives. Phase 3 is a meta.yml
field + a rollup query, deferred until the loop is live.
