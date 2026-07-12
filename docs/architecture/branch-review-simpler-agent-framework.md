# Branch review — `spike/simpler-agent-framework` vs `main`

Reviewed 2026-07-11. Scope: every change on the branch (34 commits,
133 files, ~+20k/−2.9k lines — roughly half of it documentation and
on-disk agent data, not code). Verdict format per area: **needed** /
**needed, with caveat** / **prunable**.

The branch is four layers, each depending on the previous:

```
1. MCP harness spike + identity          (bug fixes + Tier-A auto-bind)
2. Refactors 01b + 02                    (agent-level history, one run primitive)
3. Refactor 05 phases 1-2                (portable skills; phase 3 built then REMOVED)
4. Experiment rename + refactor 07       (four primitives, four artifacts)
```

## 1. MCP harness spike + identity — **needed**

| Change | Why |
|---|---|
| `condor_client.py` fail-fast on missing identity | Was an opaque 403; now errors with instructions. Bug fix. |
| `settings.ensure_identity()` + `__main__.py` auto-bind, `config_manager.get_approved_users()` | Tier A: a stock host session (repo `.mcp.json`, no CLI args) binds to the sole approved user. Without it every external-harness tool call fails. Multi-user boxes still fail fast. |
| `memory.py` identity guard | Memory resolves by user_id with no main-API call to fail on — an unresolved identity would silently use user 0's store. Real bug class. |
| `confirmation.py` `manage_bots` summary | Approve/Reject prompts for bot deploys were unreadable. Small UX fix used by human_gate. |
| `docs/architecture/mcp-harness-spike.md` | The spike's findings + QA instructions. Record. |

## 2. Refactors 01b + 02 — **needed** (the core of the branch)

- `journal.py` / `engine.py` / `sessions_index.py` / `config.py` /
  `strategy.py`: session identity `{slug}_{N}`, ALL history at the agent
  level, strategies reduced to pure playbook templates. Motivated by
  playbook A/B testing under one agent — per-playbook track records become
  a metadata filter over one comparable list. `run_once` normalizes to
  `loop + max_ticks:1` (verified: a real session, since it can place
  orders).
- `run.py` (`run_agent`) + `policies.py`: ONE execution core for every
  brain invocation, parameterized by permission policy. Deleted the
  parallel consult/delegate execution stacks.
- `delegate.py` risk resolution: per-call `risk_limits` override REPLACES
  the AGENT.md baseline; a trading delegation with neither errors loudly.
  This was the authorization gap for unattended runs.
- `agent.py` `risk_limits` baseline + web `/start` seeding order fix
  (`0548840`): the baseline was being masked by 500/5 schema defaults —
  found in a live run, regression-tested.
- `shutdown.py`: policy walk simplified strategy→agent→default to
  agent→default (winddown is session-scoped; positions don't care which
  playbook opened them) and the LLM cleanup pass rides `run_agent(AUTO)`.
- `handlers/agents/_shared.py`, MCP `routines.py`/`skills.py`/
  `trading_agent.py`: `agent_slug` scoping so an Agent subprocess reads
  ITS OWN memory/skills/routines, not the chat's. Fixes silent
  cross-contamination.
- `scripts/migrate_agent_history.py`: ran once; kept as the record of how
  the layout moved (backup `agents.pre-01b.bak/`).
- Frontend `StrategyDetail.tsx` slim-down + `AgentDetail.tsx` growth:
  follows the ontology — the agent page is the operational hub, the
  playbook page is a template view.

## 3. Audit fixes (`44e068e`) — **needed**, one part since removed

- `human_gate` fails CLOSED via `deny_gate`: a `None` permission callback
  means full auto-approve in BOTH clients (verified), so "no bot / web
  chat_id=0" previously degraded silently to auto-approve. Live bug class;
  now tested.
- MCP tool profiles: added here, **removed with curation** (its only
  consumer). Net diff vs main: none. Correctly absent.

## 4. Refactor 05, phases 1–2 (skills) — **needed**

- Spec conformance (agentskills.io): hyphenated names matching dirs,
  single-line frontmatter, description carries the routing trigger, flat
  `condor-*` metadata. Required for skills to install natively in Claude
  Code / OpenClaw / Hermes — the deployment assumption is users open those
  hosts in the condor repo.
- One host-facing library at repo-root `skills/` (+ `.claude/skills`
  symlinks) serving every consumer; old `assistants/condor/skills/*`
  deleted (moved, not lost).
- `SkillStore` tiers local > shared (`agents/_shared/skills`,
  chat-writable only; agent writes error loudly) + knowledge dedupe
  (shared `executor-mechanics`, canonical `pmm_mister_parameters.md` —
  the AGENT.md copy had drifted from the template truth).
- `scripts/migrate_skills_phase1.py`: ran once; kept as record.

**Phase 3 (curation loop): built, live-validated, then removed the same
day as over-complex.** Net remnants vs main — both deliberate keeps:
`SkillStore.patch` (provenance-stamped delta edits; safer than full-body
rewrites, which measurably collapse playbooks) and `promote_learning`
(mark a learning as folded into a skill). Both are small, manual, chat-
driven primitives.

## 5. Experiment rename — **needed** (naming debt)

`dry_run` and "experiment" were two names for one concept
(`execution_mode: dry_run` set `is_experiment`, wrote
`experiment_N.md`, `_eN` = experiment). Unified on **experiment**
everywhere: mode value, `experiments/` dir, `risk_gate(experiment=)`,
prompts, frontend labels. "dry run" survives in exactly two places as
the user synonym: `assistants/condor/AGENT.md` (chat maps the term) and
the `dry_run: true` config shorthand (still translates).

## 6. Refactor 07 — **needed** (deletes more than it adds)

Four verbs, four artifacts: consult → inline answer (nothing on disk);
delegate → flat `delegations/{date}-dN.md` (start-husk kept, own
`{slug}-dN` id namespace, disk fallback after restart); experiment →
flat `experiments/{date}-eN.md`; session → `sessions/session_N/` is
ONLY real sessions (no `kind` field, gapless numbering). Deleted:
`prune_sessions`, `TICK_KIND`, every kind filter, per-kind retention,
`SessionInfo.kind`, the web's kind-split. MCP verbs renamed
`start_session` / `start_experiment` (the vocabulary was the point).
`promote_learning` takes the bare agent slug (learnings are
agent-level). Migration ran (`scripts/migrate_refactor07.py`, backup
`agents.pre-07.bak/`); the one real tick session kept its number
(controller_id identity).

Accepted loss, named in the design doc: consult records (a rolling
`consults.log` is the deferred mitigation if it's ever missed).

## 7. On-disk agent data (`agents/*`) — **needed, with caveat**

New agents (`backtest_lab`, `revival_trader`, `funding_rate_watcher`
strategy/routines), mm_expert's second playbook (`grid_range_harvester`)
and slimmed AGENT.md, plus ~4k lines of run history (experiments,
delegation transcripts, one tick session). The history files are the
end-to-end validation record — the framework's own flows built
`grid_backtest` and `funding_logger` via routine_builder delegations —
and under the business model the track record IS the product, so
committing it is intentional. *Caveat:* transcripts will accumulate;
if the repo gets noisy, gitignore future `delegations/` /
`experiments/` content without deleting these first exemplars.

## 8. Documentation — **needed as records, two flags**

- Kept-and-current: `simpler-agent-framework.md` (the as-built
  reference), `refactor-01b/02/05/07`, `usage-patterns.md`,
  `agent-framework.md` (pre-refactor system; its §6 chat process model
  is still authoritative), `mcp-harness-spike.md`.
- Kept-as-decision-records although not adopted: `refactor-01` (tabled),
  `refactor-03` (dry-run flag decision), `refactor-04` (absorbed into
  05). Cheap to keep; they explain why the adopted designs look the way
  they do.
- `docs/strategy/*` (business-strategy, fork-vs-build,
  mcp-first-value-add, roadmap, roadmap-v2,
  strategy-engine-and-shared-intelligence): planning docs from the
  strategy sessions this branch executes against.
- **Flag 1:** `roadmap.md` vs `roadmap-v2.md` — v2 supersedes v1; v1 is
  prunable if nothing links to it.
- **Flag 2:** refactor-05's prose still describes the 4-phase plan in
  full; only its status header records that Phase 3 was removed. Fine as
  a historical doc, but don't implement from it.

## 9. Not on the branch (deliberately)

Untracked working files are NOT part of this diff and need separate
disposition: root `routines/*.py` analyses, `reports/`, `condor.db`,
`.vscode/`, and the two migration backups (`agents.pre-01b.bak/`,
`agents.pre-07.bak/` — delete both once the new layout has been trusted
for a while).

## Bottom line

Every code change traces to one of: a bug the spike surfaced, the
01b/02 ontology, skills portability, or the 07 re-carve — and the two
features that didn't earn their complexity (the curation loop, tool
profiles) were fully removed rather than left dormant. Nothing on the
branch is dead code. The only prune candidates are documentation
(`roadmap.md`) and the migration backups, plus a future policy decision
on committing run-history transcripts.
