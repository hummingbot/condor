---
name: self_improve
description: Real-time reflex for improving yourself — turn user feedback, preferences,
  and workflow patterns into memories and skills as they happen
when_to_use: After any feedback moment in a conversation — a correction, a stated
  preference, a missed pattern, a better flow discovered. Do NOT defer to end of session.
created: '2026-08-05T09:16:13Z'
source: chat
---

## Self-Improve — Real-Time Reflex

> **Scope**: reactive, in-conversation, single-turn. Fires immediately after a feedback moment — a correction, a stated preference, a missed step. For *proactive* systematic improvement of a skill (multi-round scenario testing loop), use `skill_optimizer` instead.

Run this checklist immediately after any feedback moment. Do NOT accumulate and review at end of session (token-expensive and easy to forget).

### Checklist

**1. Is this a user preference or stable fact?**
- e.g. "always show controllers before asking for params", "use 3 months as default backtest window"
- → `manage_memory(action="write", ...)` — one memory per fact, type=preference
- **Memory is YOURS ALONE** — keyed by (assistant, user), so what you write here
  is invisible to the chat and to every other agent, and there is no way to
  publish it. Write it anyway for your own work, but if the preference should
  govern how *everything* behaves, say so to the user — a cross-cutting rule
  belongs in a shared skill (below), not in one agent's memory.

**2. Is this a workflow pattern or missed step?**
- e.g. "when asked to backtest, list available controllers first"
- → First: `manage_skill(action="list")` — check if an existing skill covers it
  - If yes: `manage_skill(action="read", name=...)` to confirm, then `manage_skill(action="edit", ...)` to update it
    - **If `inherited: true`**: the skill is shared and read-only. `create` a local skill with the same name to shadow it, or ask the user to have the chat edit the published version.
  - If no: `manage_skill(action="create", ...)`
- **Before writing the body**, read `skill_authoring` — it covers anatomy, scoping, routine linking, and companion files.
- **Whose library?** Decide before creating: yours (no `agent`), a domain agent (`agent="slug"`), or all agents (`shared=True`). See `skill_authoring` for the full scoping rules.

**3. Is this a one-off or session-specific detail?**
- → Skip it. Don't pollute memory/skills with ephemeral context.

### When to escalate to `skill_optimizer`
A single reactive edit (step 2 above) is right for most feedback moments. Escalate to `skill_optimizer` when:
- The user asks for systematic or automated improvement ("make it handle edge cases", "iterate on this skill")
- The same gap has surfaced more than once across conversations
- The skill is complex enough that a single edit may introduce new blind spots

`skill_optimizer` runs a multi-round scenario-testing loop in the background — read it before triggering.

### Rules
- Save in the moment, not at the end
- One memory = one fact (no bundling)
- Always check existing skills before creating a new one
- Decide the OWNER before writing
- Shared skills land in every agent's context — publish deliberately
