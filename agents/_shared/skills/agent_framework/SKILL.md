---
name: agent_framework
description: Interact with the live agent runtime — journals, loops, instances,
  models, memory, skills, and routines.
when_to_use: User wants to read/write an agent's journal, list or control running
  agent instances (start/stop/pause/resume), inspect or change loops, query available
  models, or introspect an agent's memory/skills/routines. NOT for creating or deleting
  agents (that is agent_builder).
created: '2026-09-02T15:29:03Z'
source: chat
---

## Agent Framework — Interaction Playbook

### Tool map

Every call below is the tool's real signature. They are checked against the
live schemas by `tests/test_skill_tool_contracts.py`, because this table used to
name four parameters that do not exist — `agent`, `limit`, `entry`, `instance_id`
— and an agent following it was refused by the runtime with no idea why (SS-1).

| Intent | Tool |
|---|---|
| Read an agent's journal | `trading_agent_journal_read(agent_id="<agent_id>", section="recent", max_entries=N)` |
| Write to an agent's journal | `trading_agent_journal_write(agent_id="<agent_id>", entry_type="action", text="...", tick=N)` |
| List all agents | `manage_agents(action="list")` |
| Get one agent's config/identity | `manage_agents(action="get", agent_slug="<slug>")` |
| List the loops agents own | `manage_loops(action="list")` (each row names its `agent_slug`) |
| Get a loop's detail | `manage_loops(action="get", loop_id="<slug>.<loop_slug>")` |
| List running instances | `control_agent(action="list")` |
| Start an agent instance | `control_agent(action="start", loop_id="<slug>")` (or `"<slug>.<loop_slug>"`) |
| Stop a running instance | `control_agent(action="stop", agent_id="<agent_id>")` |
| Pause / resume | `control_agent(action="pause"/"resume", agent_id="<agent_id>")` |
| Query available LLM models | `get_available_models()` |
| Read YOUR OWN memory | `manage_memory(action="list")` then `manage_memory(action="read", name="...")` |
| Read an agent's skills | `manage_skill(action="list", agent="<slug>")` |
| Run a routine belonging to an agent | `manage_routines(action="run", name="<routine>", agent="<slug>")` |

> **An `action` entry needs its `tick`.** `tick` defaults to 0 and the tool
> refuses `action` and `canvas` entries below tick 1, so an example without it
> returns an error instead of saving. Pass the current tick number; a
> `learning` or `note` entry does not need one.

> **`agent_id` is a running instance's id, not a slug.** `control_agent` and the
> journal tools take the id `control_agent(action="list")` returns — the
> `"{agent_slug}.{loop_slug}_{N}"` form — because they address a *session*. The
> tools that address an agent's library (`manage_skill`, `manage_routines`) take
> `agent="<slug>"`. Mixing them up is the single most common refusal here.

### Steps

1. **Identify the agent slug** from the [AGENTS] index or from `manage_agents(action="list")`.
2. **Pick the right tool** from the map above. One tool call usually suffices — don't chain five tools when one answers the question.
3. **Journal reads** — pass `max_entries` to cap output (not `limit`, which is not a parameter), and `section` to pick what you want: `recent`, `learnings`, `summary`, `runs` or `full`.
4. **Controlling instances** — `control_agent(action="list")` first, to get the `agent_id` that stop/pause/resume take.
5. **Loops vs instances** — loops are the *recipe* (what to do); instances are the *running process*. Listing loops answers "what is this agent designed to do?"; listing instances answers "is it running right now?".
6. **Skills are per-agent; memory is NOT readable across agents.** `manage_skill`
   and `manage_routines` take `agent="<slug>"` and will read another agent's
   library. `manage_memory` has no such parameter and never did (SS-2): it reads
   the memory of whoever is calling, full stop. If you need another agent's
   recollection, ask that agent — `delegate(action="ask", agent="<slug>", ...)`
   — rather than trying to read its store, which is not something the runtime
   exposes.

### Do NOT use this skill for
- Creating or deleting agents → `agent_builder` skill
- Authoring or editing routines → `routine_cookbook` skill + background delegate
- Building or editing loops (the playbook code) → `loop_builder` skill
