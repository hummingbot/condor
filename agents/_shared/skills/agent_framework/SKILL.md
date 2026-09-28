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

| Intent | Tool |
|---|---|
| Read an agent's journal | `trading_agent_journal_read(agent="<slug>", limit=N)` |
| Write to an agent's journal | `trading_agent_journal_write(agent="<slug>", entry="...")` |
| List all agents | `manage_agents(action="list")` |
| Get one agent's config/identity | `manage_agents(action="get", agent_slug="<slug>")` |
| List the loops agents own | `manage_loops(action="list")` (each row names its `agent_slug`) |
| Get a loop's detail | `manage_loops(action="get", loop_id="<slug>.<loop_slug>")` |
| List running instances | `control_agent(action="list")` |
| Start an agent instance | `control_agent(action="start", loop_id="<slug>")` (or `"<slug>.<loop_slug>"`) |
| Stop a running instance | `control_agent(action="stop", instance_id="<id>")` |
| Pause / resume | `control_agent(action="pause"/"resume", instance_id="<id>")` |
| Query available LLM models | `get_available_models()` |
| Read an agent's memory | `manage_memory(action="list")` then `manage_memory(action="read", name="...")` with `agent="<slug>"` |
| Read an agent's skills | `manage_skill(action="list", agent="<slug>")` |
| Run a routine belonging to an agent | `manage_routines(action="run", name="<routine>")` |

### Steps

1. **Identify the agent slug** from the [AGENTS] index or from `manage_agents(action="list")`.
2. **Pick the right tool** from the map above. One tool call usually suffices — don't chain five tools when one answers the question.
3. **Journal reads** — pass `limit` to cap output (default can be large). Filter by date if the agent has been running a while.
4. **Controlling instances** — `control_agent(action="list")` first to get instance IDs before stop/pause/resume.
5. **Loops vs instances** — loops are the *recipe* (what to do); instances are the *running process*. Listing loops answers "what is this agent designed to do?"; listing instances answers "is it running right now?".
6. **Memory/skills are per-agent** — always pass `agent="<slug>"` when reading another agent's memory or skills, otherwise you'll read Condor's own library.

### Do NOT use this skill for
- Creating or deleting agents → `agent_builder` skill
- Authoring or editing routines → `routine_cookbook` skill + background delegate
- Building or editing loops (the playbook code) → `loop_builder` skill
