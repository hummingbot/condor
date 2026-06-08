# Agent Harness Knowledge Base

> Reference material for the **Architect Reviewer** agent (`assistants/architect_reviewer.md`).
> Captures the architecture of well-known agent harnesses and the cross-cutting patterns used
> to evaluate Condor's own agent layer. Researched June 2026; treat vendor claims as vendor
> claims. Where a fact is uncertain it is flagged.

---

## 0. What an "agent harness" is

An **agent harness** is everything built *around* a model to turn a one-turn LLM into a
governed, autonomous worker: state/memory, tool registration + exposure, the run loop,
triggers, policies/permissions, the execution environment, output channels, and control
points. The model is the *worker*; the harness is the *safety + context + control layer*
around it. Most harnesses share the same skeleton:

```
loop: build context → model call → tool dispatch → append tool result → repeat
      until final response | interrupt | budget exhausted
```

The interesting design decisions are *not* the loop — they're: how context is assembled and
compressed, how tools are scoped per agent, how state survives restarts, how agents
coordinate, and how the human stays in control.

---

## 1. Harnesses reviewed

### 1.1 OpenClaw — config-not-code coding-agent gateway
- **Shape:** self-hosted **Gateway** process that bridges messaging apps (Telegram, Slack,
  Discord, WhatsApp, iMessage, …) to AI coding agents. Open-source (MIT), large community.
- **Config model:** *"you configure, not code."* Agents/skills are **`SKILL.md`** files —
  Markdown + YAML frontmatter declaring tools, instructions, and output. Declarative,
  file-backed.
- **ACP harness:** a main OpenClaw agent can **spin up a Claude Code or Codex session**
  programmatically over ACP, hand it a task, and collect results — i.e. agent-spawns-agent
  via subprocess CLIs.
- **Also provides:** model routing, memory, channels, plugins, error recovery, deployment.
- **Relevance to Condor:** this is *almost exactly Condor's existing shape* — Telegram
  gateway + `assistants/*.md` personas + the `condor/acp/` layer that spawns
  claude-code/gemini/codex. Condor already lives in OpenClaw's design space; the redesign's
  "Main agent spawns sub-agents" is OpenClaw's ACP-harness pattern made first-class.

Sources: [OpenClaw harness guide](https://openclawlaunch.com/guides/openclaw-agent-harness),
[ACP harness explained](https://www.openclawplaybook.ai/guides/openclaw-acp-harness-explained/),
[Zylon: what is OpenClaw](https://www.zylon.ai/resources/blog/what-is-openclaw-a-practical-guide-to-the-agent-harness-behind-the-hype),
[The Register](https://www.theregister.com/ai-ml/2026/05/17/how-ai-agent-harnesses-like-openclaw-are-changing-llms-inference-and-cpus/5241530).

### 1.2 Pentagon (pentagon.run, YC) — multi-agent coordination layer
- **Shape:** a **coordination layer for humans + agents**. "Define your org structure; your
  agents run it."
- **A2A:** direct **peer messaging**, event-driven, **no polling loops**. Agents discover
  changes and tell the relevant peer directly, delegate tasks, share context, and ping the
  human only when needed. *("A backend agent discovers an API change and tells the frontend
  agent directly.")*
- **Spatial canvas:** every agent has a place; you see the whole team at a glance — who's
  active / waiting / idle — in real time.
- **Org primitives:** **Teams** (group by function with shared context), **Channels**
  (structured typed messaging), **Group Chats** (agents self-organize around a problem).
- **Memory:** persistent **institutional / "tribal" knowledge** that compounds over time
  (conversation history + decision logs).
- **Governance:** all comms happen in **readable, auditable** group chats; **granular access
  control** defines exactly what each agent can see and do.
- **Relevance to Condor:** this is the direct inspiration for the proposed "Pentagon-style
  fleet view" and the A2A bus. Pentagon validates: (a) event-driven A2A over polling,
  (b) a spatial/topology UI, (c) typed channels ≈ A2A topics, (d) auditability as a
  first-class property, (e) per-agent access scoping ≈ capability profiles.

Sources: [pentagon.run](https://www.pentagon.run/),
[Pentagon on Y Combinator](https://www.ycombinator.com/companies/pentagon).

### 1.3 Hermes Agent (Nous Research) — persistent self-improving daemon
- **Shape:** infrastructure-agnostic **persistent daemon** with one cohesive identity
  regardless of where it runs; multiple entry points (**CLI, API server, messaging
  gateway**). Self-improving via a learning loop + persistent operation.
- **Runtime boundaries (the interesting part):**
  - **Provider adapters normalize model APIs** (swap models without touching agent logic).
  - **Tool *exposure* is separated from tool *registration*** — you register a catalog once,
    then expose a *subset* per context. (This is least-privilege tooling.)
  - **Sessions are treated as infrastructure**, not ad-hoc state.
  - **Context compression creates *lineage* rather than rewriting history** — summaries point
    back to the full record instead of destroying it.
- **Relevance to Condor:** Hermes gives the cleanest vocabulary for three of Condor's open
  questions: provider adapters ≈ `ACP_COMMANDS` + pydantic-ai; *exposure-vs-registration* ≈
  the proposed capability profiles (register all MCP tools, expose a per-kind subset);
  *compression-as-lineage* is the lens to critique the journal (`write_summary` overwrites a
  running summary, but `snapshots/` preserve the full lineage — good, keep both).

Sources: [Arize: how Hermes implements an open-source harness](https://arize.com/blog/how-hermes-implements-open-source-agent-harness-architecture/),
[Architectural analysis of Hermes](https://gregrobison.medium.com/architectural-and-strategic-analysis-of-the-hermes-agent-framework-and-the-psyche-decentralized-3f7d18fb40f6),
[Hermes agent guide](https://www.analyticsvidhya.com/blog/2026/05/hermes-agent-guide/).

### 1.4 Reference points from the broader landscape (well-established)
- **Claude Code / Claude Agent SDK + subagents** — primary harness Condor runs on. Subagents
  with scoped tools + separate context windows; MCP for tools; hooks for deterministic
  control; `/loop` for recurring runs. The redesign's Main→sub-agent model mirrors the
  orchestrator+subagent pattern.
- **MCP (Model Context Protocol)** — tool/resource transport. Condor's `mcp_servers/`.
- **ACP (Agent Client Protocol)** — JSON-RPC-over-stdio to drive CLI agents. Condor's
  `condor/acp/`. OpenClaw's ACP harness is the same idea.
- **Google A2A protocol** — Agent Card / Task / Artifact / push notifications for *networked*
  agent interop. The redesign deliberately mirrors this vocabulary so external A2A is a later
  adapter, not a rewrite.
- **LangGraph** — explicit **graph** of nodes/edges with durable checkpointed state; strong
  for deterministic, resumable multi-step flows. Lens: "is the pipeline a graph with
  checkpoints, or implicit?"
- **AutoGen** — conversational multi-agent (group chat, roles). Lens: emergent coordination
  vs. wired pipelines.
- **CrewAI** — role/task/crew abstraction with a process (sequential/hierarchical). Lens: the
  redesign's "kinds" ≈ crew roles; "Main" ≈ hierarchical manager.
- **OpenAI Swarm / Agents SDK** — lightweight handoffs + routines + guardrails. Lens:
  handoff ≈ A2A request/response (`call_agent`); guardrails ≈ risk permission callback.

---

## 2. Cross-cutting evaluation lenses

Use these to review *any* agent-system design (and Condor's specifically):

| Lens | The question | Strong answer looks like |
|---|---|---|
| **Run loop** | Is there one core loop, reused by every agent type? | Single tick loop; kinds differ only by profile (Condor ✓). |
| **Tool exposure vs registration** (Hermes) | Are tools scoped per agent, least-privilege? | Register catalog once, expose subset per capability; permission callback as backstop. |
| **State & restart** | Does an agent survive a crash/restart with identity intact? | File/DB-backed sessions, durable; resumable mid-pipeline. |
| **Context assembly** | How is the prompt built each turn; what's the budget? | Deterministic pre-compute (providers/routines) + bounded memory injection. |
| **Compression = lineage** (Hermes) | Do summaries destroy or reference history? | Summaries link to full snapshots; nothing irreversibly lost. |
| **Coordination (A2A)** | How do agents hand off — poll, push, or call? | Event-driven push + typed pull + sync request/response; no busy-poll (Pentagon). |
| **Addressing** | Are messages addressed to agents or to topics/roles? | Topics/roles (swap producer without rewiring consumers). |
| **Cycle/feedback safety** | Can A2A triggers loop forever? | Debounce, max fan-out, cycle detection, budget caps. |
| **Governance** | Who can stop/limit the fleet; is it auditable? | Risk/Overseer veto + kill-switch; readable logs (Pentagon). |
| **Access control** | Can a read-only agent be made unable to act? | Capability gates + permission backstop (defence in depth). |
| **Human-in-the-loop** | When does a human get pinged; single contact? | One reporting surface (Main agent); escalate only when needed. |
| **Cost/observability** | Tokens per tick × agents × frequency; visible? | Cheap models for collectors, frontier for judgment; per-tick snapshots. |
| **Determinism boundary** | What's code vs. LLM? | Deterministic muscle (controllers/routines), LLM only for judgment (Condor ✓). |
| **Failure isolation** | Does one agent's blowup contain? | Per-agent process + `controller_id` capital isolation (Condor ✓). |

---

## 3. Condor ↔ harness mapping (quick reference)

| Concept | Condor today | OpenClaw | Pentagon | Hermes |
|---|---|---|---|---|
| Declarative agent | `agent.md` frontmatter | `SKILL.md` | agent definition | agent config |
| Spawn sub-agent | (proposed Main + `manage_trading_agent`) | ACP harness | delegation | sub-runtime |
| Drive CLI model | `condor/acp/` (ACP) | ACP harness | — | provider adapters |
| Tool scoping | (proposed capability profiles) | skill tools | granular access | exposure≠registration |
| A2A | (proposed artifact bus) | channels | peer msg / channels | — |
| Memory | Journal/Snapshot/Learnings | memory | institutional memory | compression=lineage |
| Fleet UI | card list → (proposed topology) | — | spatial canvas | — |
| Recurring run | `frequency_sec` / `/loop` | triggers | event-driven | persistent daemon |
| Gateway | Telegram + web | messaging gateway | — | CLI/API/gateway |

**Takeaway:** Condor is already an OpenClaw-class harness with Hermes-class isolation; the
redesign adds Pentagon-class coordination (A2A + topology) and Hermes-class tool scoping
(capability profiles). The pieces are well-precedented — the review should focus on *whether
Condor adopts the proven primitives faithfully* and where it diverges without reason.
