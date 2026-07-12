# Fork vs. build: should Condor fork OpenClaw or Hermes-agent instead of building its own agent runtime?

Companion to [`docs/architecture/agent-framework.md`](../architecture/agent-framework.md)
(what Condor's own runtime *is* — the ACP-subprocess session model, the MCP
tool server, the assistant/agent/skill/routine/strategy ontology) and
[`docs/strategy/business-strategy.md`](./business-strategy.md) (monetization,
audience, open-source posture, and §2's comparable landscape, which already
names Nous Research/Hermes as Condor's *closest structural comparable* in the
open-core-framework-plus-paid-hosted-layer business model). This doc asks a
narrower, purely architectural question: instead of Condor continuing to
build and maintain its own generic agent-runtime plumbing (LLM provider
integration, Telegram/messaging integration, tool-calling infrastructure),
should it fork [OpenClaw](https://github.com/openclaw/openclaw) or
[Hermes-agent](https://github.com/nousresearch/hermes-agent) and layer
Hummingbot-specific tools and Condor's own domain-agent ontology on top?

**Bottom line up front**: if a fork happens at all, it should be
**Hermes-agent, not OpenClaw** — decisively, on stack-match grounds alone.
But the stronger recommendation, after actually weighing the costs, is
**don't hard-fork either one** — the generic plumbing both projects solve
was never Condor's differentiated layer to begin with (per business-strategy
§10's own moat conclusion), and forking a fast-moving, mostly-irrelevant-for-
trading codebase trades a one-time build cost for a permanent maintenance
tax. A lighter middle path — pointing an *unforked*, upstream-tracked
Hermes-agent at Condor's existing MCP tool server as a config-only
integration — captures a meaningful slice of the benefit without any of the
fork-maintenance burden, and is worth prototyping before committing to
anything heavier. See §6.

## 1. What each project actually is

| | OpenClaw | Hermes-agent |
|---|---|---|
| **Positioning** | A personal, always-on AI assistant you run yourself, reachable over messaging apps — "the product is the assistant," not a coding tool | A personal AI agent with a "self-improving" closed learning loop (curated memory, autonomous skill creation); explicitly framed by its own docs as a superset of "Claude Code Routines" — i.e., positioned as a general automation/agent platform |
| **Primary language** | **TypeScript/Node** (monorepo: `src/`, `packages/`, `extensions/`, `ui/`) | **Python** (54.8 MB core) + TypeScript/JS for a separate Electron desktop app and Docusaurus docs site |
| **LLM provider integration** | Adapter/plugin pattern — ~70+ provider plugins under `extensions/<provider>`, plus a generalized ACP external-harness spawner (`acpx`) that can shell out to `claude`, `codex`, `gemini`, `cursor-agent`, etc. | Adapter/plugin pattern — 30 provider plugins under `plugins/model-providers/<name>/`, registered via a documented `ProviderProfile` contract; Nous's own provider is just one plugin among 30, no privileged status |
| **Messaging integrations** | ~22 channels (WhatsApp, Telegram, Slack, Discord, Signal, Teams, Matrix, IRC, Feishu, LINE, WeChat, QQ, ...), each a self-contained plugin with a documented channel-plugin SDK | 20 platform adapters (Telegram, Discord, Slack, WhatsApp, Matrix, Teams, IRC, LINE, DingTalk, WeCom, Feishu, Google Chat, Home Assistant, ...) via a uniform adapter contract |
| **Tool-calling system** | Native typed tool-plugin SDK (`defineToolPlugin`, TypeBox schemas) as the primary mechanism; MCP supported as a secondary, deliberately non-core integration surface (both client and server modes) | Native tool/toolset system (~90 core tools composed via a `TOOLSETS` registry) **plus** a mature, production-grade MCP **client** (stdio/HTTP/SSE, auto-reconnect, credential redaction, `sampling/createMessage` support) configured declaratively in `~/.hermes/config.yaml` |
| **Multi-agent / delegation** | First-class multi-agent "personas" (isolated workspace/model/session per `agentId`), message-based routing via **bindings**, agent-to-agent messaging (opt-in), documented sub-agent spawning (`sessions_spawn`) | `delegate_task` tool spawning child agents with isolated context/restricted toolsets (depth-capped, concurrency-capped), **plus** a Kanban-board-based multi-process worker/orchestrator model for distributed coordination; switchable named personas via `/personality` |
| **License** | **MIT** (copyright "OpenClaw Foundation") | **MIT** (copyright "Nous Research") |
| **Scale (stars/forks, per `gh api`)** | 382,091 stars / 80,150 forks, created Nov 2025 | 210,887 stars / 38,707 forks, created Jul 2025 |
| **Contributor concentration** | Top contributor (`steipete`) has ~3.2x the #2 contributor's commits — strongly single-lead-driven | Top contributor (`teknium1`, a Nous co-founder) has ~4.3x the #2 contributor's commits, with commit velocity (~800–1,200 commits/week recently) and commit-message style suggesting heavily AI-assisted development |
| **Self-rated maturity** | Explicit maturity scorecard: **68% = "Alpha"** overall (Coverage: Experimental 4%, Quality: Alpha 64%, Completeness: Beta 71%) | No equivalent public self-scorecard found; 143 test directories, 16 CI workflows including supply-chain/OSV scanning suggest a seriously-engineered project, but maturity wasn't self-graded the way OpenClaw's is |
| **Funding/backing** | Sponsor-funded (in-kind: OpenAI, GitHub, NVIDIA, Vercel, Blacksmith, Convex logos in README); no company entity, no commercial pricing found | Backed by Nous Research, which monetizes via the separate hosted **Nous Portal** product (business-strategy.md §2) — Portal is opt-in and loosely coupled (`hermes setup --portal`), not baked through the core |
| **Crypto/trading tooling in-tree** | None found | None found — no Solana/EVM RPC, wallet, DEX, or on-chain data tools anywhere in `tools/` |
| **Domain-agent ontology matching Condor's** | None — "agent" means isolated persona/deployment, not "specialized domain expert with its own strategies"; Condor's routine/skill/strategy split has no analog | None — subagent delegation and Kanban coordination are the closest primitives, but nothing maps to Condor's `agents/{slug}/strategies/{sslug}/` or a `TickEngine`-style persistent loop |

**One important lineage finding, not a coincidence**: Hermes-agent's own
README has an entire "Migrating from OpenClaw" section (`hermes claw
migrate`, importing `SOUL.md`/`MEMORY.md`/`USER.md` from `~/.openclaw`), and
its GitHub topics include `clawdbot`, `moltbot`, `openclaw` — the same
name-lineage OpenClaw's own `VISION.md` traces (Warelay → Clawdbot → Moltbot
→ OpenClaw). These are not two independent alternatives; Hermes-agent
appears to be a **downstream fork/absorption of the same project family**,
reimplemented (or partly ported) in Python with Nous's own additions
(multi-provider abstraction, Portal integration, Kanban coordination,
memory-provider plugin system) layered on top. Practically, this means
forking Hermes-agent doesn't mean giving up OpenClaw's ecosystem — it likely
means inheriting a compatible, Python-native descendant of it.

## 2. Head-to-head: which one would Condor fork, if either

**Hermes-agent, not OpenClaw — the stack match is close to disqualifying on
its own.** Condor's actual differentiated logic — Hummingbot strategy
execution, Gateway/Solana glue, the `TickEngine` tick loop, Swig integration
— is Python. Forking OpenClaw means either a full rewrite of that logic into
TypeScript, or maintaining a permanent cross-language RPC boundary between a
TypeScript agent-runtime and a Python trading-domain service — a much larger
and more awkward commitment than anything the fork was meant to save.
Hermes-agent is already Python, already async (FastAPI/uvicorn-based
dashboard, `httpx`, `websockets` in its dependency list), and already uses
`uv`/modern Python packaging — the same tooling generation Condor's own
stack already sits in.

Three more factors reinforce this, beyond the headline stack match:

- **Near-zero-cost integration with what Condor has already built.**
  Hermes-agent's MCP client is mature enough (auto-reconnect, credential
  redaction, per-server timeouts) that pointing it at Condor's *existing*
  MCP tool server — the one already exposing Hummingbot/trading tools today
  — is a one-block `mcp_servers:` config entry, not new code. OpenClaw's MCP
  support is explicitly secondary/pragmatic by its own docs (*"pragmatic MCP
  support without duplicating existing agent, tool, ACPX, plugin, or ClawHub
  paths"*) — usable, but not the tool substrate its own plugin ecosystem is
  built around, so Condor's trading tools would more naturally be reimplemented
  as native `defineToolPlugin` TypeScript plugins instead of reused as-is.
- **Conceptual proximity to Condor's own ontology.** Hermes' skills-as-
  markdown system and its footprint-ladder guidance ("prefer a skill over a
  new core tool") map fairly directly onto Condor's own skill/routine split
  (architecture doc §2–3). OpenClaw's closest analog — multi-agent personas
  and bindings — is a different shape (isolated deployments, not specialized
  domain experts sharing one runtime), and would need more translation work.
- **Maturity signal, weighed carefully.** OpenClaw's self-published 68%
  "Alpha" scorecard is unusually honest, but it's also a direct admission
  that large parts of the surface aren't production-hardened yet — a real
  consideration for forking something meant to carry real trading capital
  and API keys. Hermes-agent has no equivalent public self-grade, but its CI
  posture (OSV scanning, supply-chain-audit workflow, exact-pinned
  dependencies after a documented real supply-chain incident) reads as more
  security-conscious specifically in the dimension that matters most for a
  fork meant to hold financial credentials.

**One real strategic wrinkle specific to Hermes-agent, worth naming
explicitly rather than glossing over**: business-strategy.md §2 already
identifies Nous Research/Hermes as Condor's *closest structural comparable*
in the open-core-framework-plus-paid-hosted-Portal business model — i.e.,
potentially the nearest thing Condor has to a direct competitor. Forking
Hermes-agent means building Condor permanently on top of a comparable's own
core repository — a real roadmap-dependency and brand-optics risk ("is
Condor just Hermes with a trading skin?") that doesn't exist if Condor stays
on its own runtime, or even if it forked OpenClaw instead (a project with no
directly competing hosted-product ambitions in the trading space). This
doesn't override the stack-match argument, but it's a cost specific to the
Hermes choice that a pure technical comparison would miss.

## 3. What forking would actually buy Condor, mapped onto the existing ontology

Being concrete about what maps cleanly vs. what still has to be built,
regardless of which project (if either) gets forked:

**Maps reasonably cleanly (would likely be inherited, not rebuilt):**

- Multi-provider LLM abstraction (30 providers on Hermes, no vendor lock-in)
  — Condor's current ACP-subprocess model already only supports Claude
  (`claude-agent-acp`, architecture doc §6); this is a genuine capability
  gain, not just a maintenance-avoidance one.
- Telegram integration — both projects' Telegram adapters are mature,
  full-featured (threads, streaming edits, inline keyboards, slash
  commands) — likely a strict upgrade over Condor's own hand-rolled bot.
- MCP client infrastructure — Hermes' is production-grade; Condor currently
  is primarily an MCP *server* (exposing tools), not a client consuming
  others' MCP servers, so this would be new capability, not a duplicate.
- Subagent delegation primitives (`delegate_task`, depth/concurrency caps)
  — conceptually close to Condor's own `consult`/`delegate` split
  (architecture doc §1), though the semantics would need reconciling (see §5).
- Skills-as-markdown — close enough to Condor's own skill system that
  Condor's existing `SKILL.md` content could plausibly port with light
  reformatting.

**Does not exist in either project and must be built by Condor regardless
of the fork decision:**

- The assistant/agent split itself (architecture doc §1) — neither
  project's "agent" concept means "specialized domain expert with its own
  tool allowlist and `when_to_consult` routing."
- The strategy layer and `TickEngine` (architecture doc §4) — nothing in
  either codebase resembles a persistent, periodic tick-loop trading
  strategy runtime with its own journal/learnings/snapshot accumulation
  (per the earlier session/turn-mechanics research in this doc series).
  This is Condor's actual hardest-won, most differentiated engineering —
  and it's trading-domain-specific in a way neither generic agent framework
  has any reason to have solved.
- All Hummingbot/Gateway-specific tools (order placement, position
  management, backtest execution, Swig wallet integration) — zero head
  start in either repo, confirmed directly (no crypto/on-chain tooling
  found in either).
- Condor's routine system (global + per-agent-local, architecture doc §3)
  and the business-strategy.md §11a proposal to generalize skills the same
  way — again, no analog in either project to inherit.

**The upshot**: forking either project buys the generic chat-agent
plumbing layer, not the differentiated trading-agent layer — and per
business-strategy.md §10's own conclusion (the moat is brand/community/
hosted-convenience/capital-network-effects, not the code), that plumbing
layer was never going to be Condor's source of defensibility anyway. That
cuts both ways: it means forking doesn't give away anything strategically
important, but it also means the *benefit* of forking is bounded to
"maintenance-hours saved on a non-differentiated layer," not "acquiring a
moat."

## 4. The real costs of forking

- **Enormous, mostly-irrelevant surface area.** Both codebases are much
  bigger than what a trading-agent fork needs — video/voice generation,
  browser/computer-use automation, Electron desktop apps, Home Assistant
  and Chinese-market platform integrations (WeChat/QQ/DingTalk/WeCom), a
  Kanban dashboard with its own systemd unit, full Docusaurus documentation
  sites. Hermes-agent's repo is ~456 MB including history; OpenClaw's has
  ~180+ extension directories. A fork means either deleting most of this
  (fine on day one, but upstream treats breadth as a stated *goal* — both
  `AGENTS.md`/`VISION.md` documents say so explicitly — so the irrelevant
  surface keeps growing if any tracking of upstream continues) or carrying
  it as permanent dead weight.
- **Upstream velocity makes divergence expensive, not a one-time cost.**
  OpenClaw ships continuous same-day multi-commit releases; Hermes-agent
  runs at roughly 800–1,200 commits/week recently. A fork diverges from
  upstream within days of branching. Any attempt to keep pulling upstream
  security/provider fixes becomes an ongoing integration tax; not tracking
  upstream means quietly losing future improvements (and importantly,
  future security patches to a codebase now holding real API keys and
  trading capital) — this is a recurring cost, not a startup cost.
- **Condor already exists and is already shipping.** This isn't a
  greenfield decision — business-strategy.md §1 establishes
  `condor.hummingbot.org` is live, and this repo is already dogfooding real
  Botcamp Solutions client mandates (USDM1) on Condor's own current runtime.
  A hard fork is a replatforming project with real switching costs: every
  existing agent, skill, routine, and strategy would need porting, and the
  session/context mechanics this doc series has already documented in
  depth (architecture doc §6) would need to be rebuilt or reconciled against
  a different host runtime's session model.
- **Bus-factor and governance risk transfers, not disappears.** Both
  projects are dominated by one contributor (~3–4x the next-largest); that
  concentration risk doesn't go away by forking, it just becomes a
  dependency on a single external maintainer's continued direction and
  availability instead of Condor's own team's.
- **The Hermes-specific strategic-dependency risk from §2**, restated:
  building on a comparable's own repo is a real roadmap and brand-optics
  cost, independent of the technical merits.

## 8. A narrower option: import Hermes' `AIAgent` class as a library, not the repo

Worth checking explicitly, since it's a real middle ground between "fork the
whole repo" and "build everything ourselves": Hermes-agent's subagent
delegation (`tools/delegate_tool.py`) works by spawning child `AIAgent`
instances — could Condor `pip install hermes-agent` and import that class
directly to power part of its own runtime, without touching the rest of the
repo? Verified directly against the source (`run_agent.py`, PyPI package
`hermes-agent` v0.18.0):

**No — not advisable, and the reasons are specific, not just "it's someone
else's code":**

- **No stable, namespaced import path.** There's no `hermes_agent` package;
  the only way to get `AIAgent` is `from run_agent import AIAgent` — a
  bare top-level module name (`run_agent.py`, ~6,000 lines) with an obvious
  collision risk in any codebase that has or might add its own module by
  that name, and zero `__init__.py` re-export layer signaling a supported
  public API.
- **Not actually standalone.** Even in its most minimal configuration,
  constructing `AIAgent` reads from `~/.hermes/config.yaml` via Hermes' own
  `hermes_cli.config.cfg_get()` and touches a `~/.hermes` home directory
  through `hermes_constants.get_hermes_home()`. Core (non-extra)
  dependencies include a full `fastapi`/`uvicorn` web stack, pulled in
  regardless of whether the embedding application ever serves HTTP.
- **Custom tools can't be handed in as a constructor argument at all.**
  `AIAgent.__init__` takes toolset *names* (`enabled_toolsets`), resolved
  against a global, import-time tool registry. Wiring in Condor's own
  Hummingbot-wrapping tools would mean authoring them as Hermes-style
  registry-registered modules or plugin-manifest packages — exactly the
  coupling this option was meant to avoid.
- **MCP support exists but writes into shared global state, not a
  per-instance config.** `register_mcp_servers(servers: dict)`
  (`tools/mcp_tool.py`) is callable programmatically (bypassing
  `~/.hermes/config.yaml`), so pointing it at Condor's own MCP server is
  technically possible — but it's an undocumented internal function, not a
  constructor parameter, and it registers into the same global registry
  every `AIAgent` instance shares.
- **Zero documentation anywhere treats this as a supported use case.**
  README, `AGENTS.md`, `CONTRIBUTING.md`, and `docs/` all target running
  `hermes` as an installed CLI/gateway/dashboard application. The one
  library-style code example (a 4-line snippet in `run_agent.py`'s own
  module docstring) is the only signal it can be done at all, not evidence
  it's meant to be depended on.

**What's actually worth taking from this, consistent with §7's overall
recommendation**: read `run_agent.py` and `tools/delegate_tool.py` for
*design reference* — the streaming-callback hooks, the subagent-delegation
pattern (depth/concurrency caps, isolated child context), and the
programmatic `register_mcp_servers` entry point are all reasonable patterns
to reimplement in Condor's own runtime where they fit. Importing `AIAgent`
itself as a load-bearing dependency means depending on an undocumented,
~90-parameter, config-file-coupled internal class inside someone else's
monolith, with no library-stability contract across any of its 8 PyPI
releases — a worse trade than either forking outright or building the
equivalent piece directly.

## 5. Reconciling delegation semantics, if a fork is pursued

Worth flagging even under the "don't hard-fork" recommendation, in case a
future team revisits this: Hermes' `delegate_task` and Condor's `consult`/
`delegate` split (architecture doc §1) look similar but differ in a load-
bearing way. Hermes' delegation is synchronous-from-the-parent's-view (the
parent blocks until children complete and only sees a summary) with a hard
depth cap of 1 by default. Condor's `delegate` is explicitly asynchronous
and backgrounded — the calling session gets a `task_id` back immediately and
must poll `delegate(action="get", ...)` later (per the session/context
research earlier in this doc series) — while `consult` is the synchronous,
human-confirmation-gated mode. Porting Condor's ontology onto Hermes'
primitives would mean `consult` maps reasonably onto `delegate_task`'s
blocking model, but Condor's async `delegate` (and its Telegram-push-on-
completion behavior) would need genuinely new plumbing on top of Hermes'
synchronous-child model, not a renaming exercise.

## 6. A middle path worth prototyping: composition, not forking

Because Hermes-agent's MCP client can already point at an arbitrary MCP
server via a config block, there's a materially lower-risk option between
"keep building Condor's own plumbing" and "fork and replatform": run
**unforked, upstream-tracked Hermes-agent** as an optional alternate chat
frontend, configured with Condor's existing MCP tool server as one of its
`mcp_servers` entries — no fork, no repo to maintain, upstream security and
provider updates keep landing for free via normal version upgrades.

What this buys: Hermes' multi-provider model support and its mature
Telegram/messaging adapters, available to try against Condor's real trading
tools with a config change, not a build project.

What it doesn't buy, and this is the real limitation: an unforked
Hermes-agent has no idea Condor's assistant/agent/skill/routine/strategy
ontology exists. It would see Condor's Hummingbot tools as a flat toolset
with no `[AGENTS]` index, no skill routing, no domain-agent context
construction (`build_agent_context`, per the earlier session-mechanics
research) — exactly the differentiated layer §3 establishes has no analog
to inherit either way. Reconstructing even a thin version of that ontology
as a Hermes-side plugin is a real, scoped project — smaller than a full
fork, but not zero, and it starts to blur back toward "fork" the more of it
gets built.

**Recommendation for this option specifically**: worth a low-cost,
time-boxed prototype (point a stock Hermes-agent install at Condor's
existing MCP server, see what a Telegram chat against it actually feels
like) to get real signal on whether the generic-plumbing gap is big enough
to justify further investment here — before committing to either a fork or
continued investment in Condor's own runtime layer.

## 7. Recommendation

1. **Do not hard-fork OpenClaw.** The TypeScript/Python stack mismatch with
   Condor's actual trading-domain logic makes this the more expensive path
   for less benefit than Hermes-agent, with no offsetting advantage found
   that isn't also available (in Python) via Hermes.
2. **Do not hard-fork Hermes-agent either, at least not now.** The generic
   plumbing it would contribute was never Condor's differentiated layer
   (§3), the fork-maintenance tax against an 800–1,200-commits/week
   upstream is real and ongoing (§4), Condor already has a shipping product
   with real client mandates that a replatform would disrupt (§4), and
   there's a Hermes-specific strategic-dependency risk from building on a
   named comparable's own repository (§2).
3. **Prototype the composition path (§6) cheaply before deciding anything
   bigger.** Point an unforked Hermes-agent at Condor's existing MCP tool
   server and see directly what gap (if any) remains between that and
   Condor's own current Telegram/chat experience. This produces real
   evidence — the same discipline this doc series has favored throughout
   (e.g. business-strategy.md §7's "validate before committing" sequencing)
   — at a fraction of the cost of a fork-and-replatform decision made on
   priors alone.
4. **Continue building Condor's own runtime for the layer that actually
   matters**: the assistant/agent/skill/routine/strategy ontology, the
   `TickEngine`, and Hummingbot/Gateway/Swig-specific tools have no
   analog in either project and are Condor's real engineering investment
   either way — no fork decision changes that scope.
