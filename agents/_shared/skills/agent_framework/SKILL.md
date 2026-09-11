---
name: agent_framework
description: Interact with the live agent runtime — journals, strategies, instances,
  models, memory, skills, and routines.
when_to_use: User wants to read/write an agent's journal, list or control running
  agent instances (start/stop/pause/resume), inspect or change strategies, query available
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
| Get one agent's config/identity | `manage_agents(action="get", agent="<slug>")` |
| List strategies an agent owns | `manage_strategies(action="list", agent="<slug>")` |
| Get a strategy detail | `manage_strategies(action="get", agent="<slug>", strategy="<name>")` |
| List running instances | `control_agent(action="list")` |
| Start an agent instance | `control_agent(action="start", agent="<slug>")` |
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
5. **Strategies vs instances** — strategies are the *recipe* (what to do); instances are the *running process*. Listing strategies answers "what is this agent designed to do?"; listing instances answers "is it running right now?".
6. **Memory/skills are per-agent** — always pass `agent="<slug>"` when reading another agent's memory or skills, otherwise you'll read Condor's own library.

### Do NOT use this skill for
- Creating or deleting agents → `agent_builder` skill
- Authoring or editing routines → `routine_cookbook` skill + background delegate
- Building or editing strategies (the playbook code) → `strategy_builder` skill
