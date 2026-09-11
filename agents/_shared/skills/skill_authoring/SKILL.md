---
name: skill_authoring
description: How to write, publish, and maintain skills — the playbook for authoring
  playbooks.
when_to_use: Any time a new skill needs to be created, an existing one needs to be
  edited, a skill needs to be scoped to an agent vs shared, or a routine needs to
  be linked. Also read this before deciding whether something should be a skill vs
  a memory vs a routine.
created: '2026-09-01T21:33:49Z'
source: chat
---

## What a skill is (and what it is not)

A **skill** is an advisory playbook: *when* to apply a flow and *how* to execute it step by step. It is reusable know-how captured once so it never has to be re-derived. It is NOT:

- A memory (memories are facts about the **user**; skills are how **you** operate).
- A routine (a routine is executable Python; a skill is prose that may *reference* one).
- A bypass of normal confirmation rules — a playbook is advisory, dangerous actions still require confirmation.

---

## The tool

All skill operations go through `manage_skill`. Key actions:

| Action | When to use |
|--------|-------------|
| `list` | See the full index before creating — check the name doesn't already exist. |
| `read` | Pull a full playbook before following or editing it. |
| `search` | Find skills by keyword when the exact name is unknown. |
| `create` | Add a new playbook (name + description + when_to_use + body, all required). |
| `edit` | Patch any field of an existing skill — only send what changed. |
| `delete` | Remove a skill that is no longer valid. |
| `write_file` | Attach a companion file (config template, reference table) to a skill. |
| `read_file` | Pull a companion file on demand — progressive disclosure, not in context until requested. |

**Never hand-write a skill in the filesystem.** `manage_skill` is the single entry point — it handles storage, indexing, and routine validation automatically.

---

## Anatomy of a well-written skill

### `name` — the lookup key
- kebab-case, lowercase, specific: `grid-in-band-walk`, not `grid` or `trading`.
- Must be stable — agents and other skills reference it by name.

### `description` — one line
- Summarises what the skill covers, not when to use it.
- Shown in the index (`[SKILLS]`) injected at the top of every turn — keep it tight.

### `when_to_use` — the trigger
- Describes the exact conditions that should make an agent reach for this skill.
- Be concrete: list trigger phrases, user intents, and edge cases. Vague triggers get ignored.
- Also shown in the index. This is the gate — if it is too broad, the skill is read when it shouldn't be; too narrow and it is never read.

### `body` — the playbook
- **Lead with a seat check when the skill is shared.** Different agents have different authorities (the chat does intake only; the domain agent owns the decision). Say so at the top.
- Structure with `##` sections. Common sections: context/what this covers → step-by-step flow → tool contract (exact calls) → rules / non-negotiables.
- Include **exact tool calls** where precision matters — copy-paste ready, not "call the routine with the right config".
- State **what NOT to do** as explicitly as what to do.
- Keep it scannable: tables for comparisons, code blocks for calls, bullet lists for rules.

---

## Scoping: shared vs agent-owned

```
manage_skill(action="create", ..., shared=True)   # → shared library, every agent reads it
manage_skill(action="edit",   ..., shared=False)  # → moves it back to Condor's own library
manage_skill(action="create", ..., agent="slug")  # → that agent's private library
```

**Shared** (`shared=True`): lands in every agent's injected `[SKILLS]` index. Use when:
- The playbook governs a flow that any agent might encounter (backtest intake, skill authoring, log triage).
- The steps are seat-aware (the body explains what each role should do differently).

**Agent-scoped** (`agent="slug"`): only that agent's library. Use when:
- The playbook is domain-specific and meaningless elsewhere (`mm_bot_report` belongs to `market_making_expert`).
- Publishing it to the shared library would add noise to every other agent's context.

**Rule**: default to agent-scoped. Publish to shared deliberately — shared playbooks cost every agent context tokens on every turn.

---

## Linking a routine

If the skill's primary action is to run a routine (not hand-roll), link it formally:

```
manage_skill(action="edit", name="my-skill", references_routine="my_routine")
```

- `routine_ok: true/false` is returned on `read` — if false, the routine was deleted; fix the skill before invoking it.
- The linked routine shows as a clickable chip in the dashboard's skill panel — agents and users can navigate directly to it.
- To unlink: `references_routine=""`.
- A skill may reference only one routine. If the flow uses several (e.g. `backtest_chart` + `backtest_compare`), link the primary one and mention the others in the body.

---

## Companion files

For large reference tables, config templates, or secondary playbooks that should not always be in context:

```
manage_skill(action="write_file", name="my-skill", file="reference.md", content="...")
manage_skill(action="read_file",  name="my-skill", file="reference.md")
```

`read` lists companion `files` — pull one only when needed (progressive disclosure). The main playbook stays small; the detail lives in the file.

---

## Editing an existing skill

1. **Always `read` first** — never edit from memory or the index summary alone.
2. Send only the fields that changed (`edit` is a patch, not a replace).
3. When the playbook is shared, consider the effect on every agent that reads it — a seat-specific addition should be in a branch of the body, not replacing the general flow.
4. After editing a shared skill, briefly verify the `when_to_use` still gates correctly: would an unrelated agent accidentally match it?

---

## Decision tree: skill vs memory vs routine

```
Is it a fact about the user?          → manage_memory
Is it executable Python?              → manage_routines (create_routine / delegate to build it)
Is it reusable step-by-step know-how? → manage_skill  ← you are here
```

A skill that says "run this routine" + links it with `references_routine` is the right combination for a flow that has both prose guidance (when/why) and executable implementation (the routine).

---

## Rules

- **List before create** — avoid duplicate names.
- **Read before edit** — never patch blind.
- **Scope before publish** — default agent-scoped, escalate to shared only when genuinely universal.
- **One routine link per skill** — link the primary; document secondaries in the body.
- **Keep `when_to_use` honest** — it is injected in every turn; a lying trigger wastes context and confuses routing.
- **Never write skill files to the filesystem directly** — use `manage_skill` exclusively.
