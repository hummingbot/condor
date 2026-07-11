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

## 3. Recommendation

Three phases, each independently shippable. The principle throughout:
**learnings are the capture channel, skills are the curated product, and the
pen is held by a curation pass — never by the in-run agent.**

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
   sessions, or a `/curate` command; mechanically it is a delegation to the
   agent itself (or a shared `skill_curator` flow) with an AUTO policy —
   serverless, so no risk gate needed:
   - Input: the agent's learnings.md + the last N session journals/
     transcripts + current skills index.
   - Mandate: **delta edits only** — append/refine a specific section, merge
     a duplicate, retire a stale bullet; never rewrite a skill wholesale;
     only distill patterns seen in **≥2 sessions** (single-episode reflexes
     stay in learnings). Consumed learnings are marked (moved to a
     "promoted" section) so the 20-cap stops evicting valuable ones.
   - Output: `manage_skill(action="edit"/"create")` calls on the agent's
     **local** tier only.
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

Phase 1 is small (SkillStore tier resolution + scope param + content moves —
refactor-04 §4-6 already specifies it) and pays immediately by deleting the
three-way regime duplication. Phase 2 is one new flow (curator prompt +
trigger + git commit step) on existing primitives. Phase 3 is a meta.yml
field + a rollup query, deferred until the loop is live.
