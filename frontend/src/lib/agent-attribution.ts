// ── Whose trading is this? (FEAT-096) ──
//
// Attribution in this browser is a client-side join, and this is the second one
// of the same shape: an `ExecutorInfo` carries no `bot_name`, so `PerfBrowser`
// reconstructs the bot from `controller_id` and passes the answer into the leaf.
// Which *agent* owns a record is the same join one level up, over the same kind
// of fact — a plain string on a record the browser already holds.
//
// The rule itself is not invented here. It is enforced in Python, at the tick's
// permission callback (`condor/agents/ownership.py`, `risk.py`), and
// `GET /agents/fleet-map` ships it out as data: the namespace that proves bot
// ownership, the legacy names a strategy declares (which no name rule could
// derive), and the agent ids that tag standalone executors. What lives here is
// only the matching.
//
// Nothing here fetches or renders (the ARCH-300 split), so every rule below is
// reachable from a test.

/**
 * One mutating tool call an agent made, as the page reports it (FEAT-097).
 *
 * The wire shape of `condor.agents.actions.AgentAction`, mapped straight
 * through: every field is one word, so there is no camelCase pass to get wrong.
 * The `summary` is rendered in Python by the confirmation prompt's own
 * renderer, which is why nothing here interprets a tool's arguments.
 */
export interface AgentActionRow {
  /** Joins to `snapshot_{tick}.md` — the tick this deed happened on. */
  tick: number;
  /** Epoch **seconds**. */
  at: number;
  /** `"create_lp_executor"`, MCP prefix stripped. */
  tool: string;
  /** `"create_lp_executor"` or `"manage_bots:deploy"` — the queryable key. */
  verb: string;
  /** The human line: `Create grid executor on SOL-USDC ($100)`. */
  summary: string;
  ok: boolean;
  /** Clipped failure text when `!ok`, else `""`. */
  error: string;
}

/** The tick loop currently driving a strategy, or `null` when none is. */
export interface LiveLoop {
  agentId: string;
  sessionNum: number;
  /** `running` | `paused`. No loop at all reads as *idle*. */
  status: string;
  tickCount: number;
  /** Epoch **seconds**, or 0 when the loop has not ticked yet. */
  lastTickAt: number;
  frequencySec: number;
  /** What the agent last *said* — the journal's `Last action:` line. */
  lastAction: string;
  /**
   * What the agent last **did**, or `null` when it has done nothing.
   *
   * The deed and the words are two statements, not one: `lastAction` is the
   * model's own narration, this is a record of a tool call that ran. A session
   * that predates the log reads `null` — nothing is backfilled.
   */
  lastDid: AgentActionRow | null;
  lastError: string;
}

/** One `(agent, strategy)` and everything needed to attribute its work. */
export interface FleetOwner {
  /** `"brigado.brl_mm"` — the scope-tree node id and the join key. */
  runKey: string;
  agentSlug: string;
  agentName: string;
  strategySlug: string;
  strategyName: string;
  /** `{agentSlug}-{strategySlug}`: the prefix that proves bot ownership. */
  namespace: string;
  /** Configured bot names outside the namespace — the legacy escape hatch. */
  declaredBots: string[];
  /** Every `"{runKey}_{N}"` on disk: the executor `controller_id` tag set. */
  agentIds: string[];
  live: LiveLoop | null;
}

/**
 * The `-YYYYMMDD-HHMMSS` a deploy appends to the name that was asked for.
 *
 * `condor/agents/ownership.py:strip_deploy_suffix`, verbatim. The namespace
 * rule below does not need it — an instance is a tagged sibling and matches by
 * prefix either way — but a *declared* legacy name does: `old_hand_bot` is
 * owned, and `old_hand_bot-20260731-101500` is the same bot.
 */
const DEPLOY_SUFFIX = /-\d{8}-\d{6}$/;

export function stripDeploySuffix(name: string): string {
  return (name || "").replace(DEPLOY_SUFFIX, "");
}

/**
 * A running bot's name and every asked-for name it can have come from.
 *
 * The suffix is appended **per deploy**, and a redeploy of a name that already
 * carries one appends a second: `pmm-king-btcbrl` becomes
 * `pmm-king-btcbrl-20260903-181000`, and redeploying *that* becomes
 * `pmm-king-btcbrl-20260903-181000-20260903-151237`. `stripDeploySuffix` is
 * anchored at the end and mirrors the Python one call for call, so it takes one
 * suffix off — which is right for the name a deploy was given and one short for
 * the name a redeploy was given.
 *
 * That one is not a corner case: it is the bot that has been running longest,
 * which is exactly the bot with the most trading under it, and it arrived at
 * `/bots` credited to nobody. So the deed lookup asks for every name in the
 * chain rather than for one, longest first — the most specific record that
 * exists wins, and a bot that was deployed once is unaffected because its chain
 * is the two names it always had.
 *
 * The two enforced rules do not need this: they match by prefix, and a prefix
 * survives any number of suffixes.
 */
export function deployNameChain(name: string): string[] {
  const chain: string[] = [];
  let current = (name || "").trim();
  while (current && !chain.includes(current)) {
    chain.push(current);
    current = stripDeploySuffix(current);
  }
  return chain;
}

/**
 * True for the namespace itself, a tagged sibling, and any deployed instance.
 *
 * `condor/agents/ownership.py:in_namespace`, verbatim: `brigado-brl_mm`,
 * `brigado-brl_mm-btc` and `brigado-brl_mm-btc-20260731-101500` are all owned,
 * and `brigado-brl_mm_v2` is *not* — slugs never contain `-`
 * (`condor/frontmatter.py:slugify` maps it to `_`), so the `-` delimits the
 * namespace unambiguously and two strategies can never claim the same bot.
 */
export function inNamespace(name: string, ns: string): boolean {
  if (!name || !ns) return false;
  return name === ns || name.startsWith(`${ns}-`);
}

/**
 * The run key of the strategy that owns this bot, or `""` for none.
 *
 * Longest namespace first. The `-` delimiter already makes the rule
 * unambiguous, so this can only matter if a slug convention ever changes; it
 * costs one sort of a handful of owners and mirrors `partition_instances`
 * (`condor/fetchers/bot_performance.py`), which orders the same way for the
 * same reason.
 *
 * The declared fallback matches by the *same* rule rather than by equality,
 * because that is what the runtime does (`BotLedger.owns` calls `in_namespace`
 * on each declared name): a legacy base and its tagged siblings are one bot's
 * family, and crediting only the base would strand its instances.
 */
export function agentOfBot(owners: FleetOwner[], botName: string): string {
  // The name-only half of `attributionOf` below, which owns the rule: passing no
  // deed index leaves exactly the two enforced rules, which is what this asks.
  return attributionOf(owners, null, botName).runKey;
}

/**
 * The run key of the strategy whose session tagged this executor, or `""`.
 *
 * A standalone executor an agent created carries its session's `agent_id`
 * (`"{runKey}_{N}"`) as its `controller_id` — `create_*_executor` is refused
 * otherwise, so there is no untagged agent executor and no guessing to do here.
 * A controller's `controller_id` is a config id and matches nothing, which is
 * right: a controller is attributed through its *bot*.
 */
export function agentOfControllerId(owners: FleetOwner[], controllerId: string): string {
  const id = (controllerId || "").trim();
  if (!id) return "";
  for (const owner of owners) {
    if (owner.agentIds.includes(id)) return owner.runKey;
  }
  return "";
}

// ── Whose trading is this, when no name proves it (FEAT-106) ──

/**
 * One record Condor wrote down about something it did (FEAT-105/106).
 *
 * The wire shape of `condor.agents.deed_index.OwnerRef`. `at` is epoch
 * **seconds**, like every other timestamp the fleet map ships.
 */
export interface DeedRef {
  /** `"condor.chat"`, `"brigado.delegation"`, `"directional_trader.ema_trend_loop"`. */
  runKey: string;
  /** The conversation id, the delegation task id, `"ui"`, or `"s3"`. */
  runId: string;
  at: number;
}

/**
 * Everything `GET /agents/fleet-map` answers with: the rules, and the records.
 *
 * One object rather than two calls because they are one snapshot — an owner
 * list and a deed index taken a poll apart could disagree about what is on
 * disk, and the join below reads both.
 */
export interface FleetMap {
  owners: FleetOwner[];
  deeds: DeedIndex;
}

/** What Condor's own logs can attribute, and how far back they reach. */
export interface DeedIndex {
  /** Bot **base** names (no deploy suffix) → the run that made it. */
  bots: Record<string, DeedRef>;
  /**
   * Epoch **seconds** before which Condor did not record everything it did, or
   * `0` when it never has.
   *
   * The one timestamp that splits the old `Unattributed` bucket in two: a
   * record older than this predates the ledger and cannot be judged; a record
   * newer than it, with no deed, was made by something that is not Condor.
   */
  since: number;
}

/**
 * *How* a record was attributed, which is not a detail.
 *
 * `namespace` and `declared` are **proofs**: the tick's permission callback
 * refused everything else, so they cannot be wrong. `deed` is a **report**: it
 * says what was recorded, and a record can be stale (a bot destroyed and its
 * name reused). A reader deciding whether to stop a bot deserves to know which
 * of the two they are looking at, which is why this rides beside the run key
 * rather than being folded into it.
 */
export type Provenance = "namespace" | "declared" | "deed" | "none";

/** A run key and the kind of evidence behind it. `""` exactly when `how` is `none`. */
export interface Attribution {
  runKey: string;
  how: Provenance;
}

const UNOWNED: Attribution = { runKey: "", how: "none" };

/** What a deed-attributed row says about itself when you ask it. */
export const DEED_TITLE = "attributed by a recorded deed, not by name";

/**
 * The rule of {@link attributionOf}, with the owner list already prepared.
 *
 * Ask a fold for this once and call it per record; ask {@link attributionOf}
 * for a single answer.
 */
export type Attributor = (botName: string, controllerId?: string) => Attribution;

/**
 * {@link attributionOf} with its loop-invariant half hoisted out (PERF-331).
 *
 * `owners` does not change while a population is being folded, but the rule
 * asks the same two questions of it per record: an ordering by namespace
 * length, and whether any owner's `agentIds` contains a tag. Both were paid per
 * record — an array copy, a sort, and an `Array.includes` scan across every
 * owner — inside loops that run over every executor the fleet has ever had, and
 * re-run whenever the executors socket frame lands. So the preparation happens
 * here, once, and the closure below does the matching and nothing else.
 *
 * **The answers are identical, not merely equivalent.** The comparator, the
 * array it sorts and the sort's stability are the same, so `byLength` is the
 * same order the per-record sort produced; and the id map is filled in owner
 * order keeping the first writer, which is the owner the linear scan in
 * {@link agentOfControllerId} returned. The rule order — namespace, declared,
 * controller-id tag, deed chain — is unchanged.
 */
export function attributionIndex(
  owners: FleetOwner[],
  deeds: DeedIndex | null | undefined,
): Attributor {
  const byLength = [...owners].sort((a, b) => b.namespace.length - a.namespace.length);
  // First writer wins, mirroring the first-match linear scan it replaces.
  const runByAgentId = new Map<string, string>();
  for (const owner of owners) {
    for (const id of owner.agentIds) {
      if (!runByAgentId.has(id)) runByAgentId.set(id, owner.runKey);
    }
  }
  const bots = deeds?.bots;
  return (botName: string, controllerId: string = ""): Attribution => {
    const name = stripDeploySuffix((botName || "").trim());
    if (name) {
      for (const owner of byLength) {
        if (inNamespace(name, owner.namespace)) return { runKey: owner.runKey, how: "namespace" };
      }
      for (const owner of byLength) {
        if (owner.declaredBots.some((declared) => inNamespace(name, declared))) {
          return { runKey: owner.runKey, how: "declared" };
        }
      }
    }
    const id = (controllerId || "").trim();
    const tagged = id ? (runByAgentId.get(id) ?? "") : "";
    if (tagged) return { runKey: tagged, how: "namespace" };
    for (const candidate of deployNameChain(botName)) {
      const deed = bots?.[candidate];
      if (deed) return { runKey: deed.runKey, how: "deed" };
    }
    return UNOWNED;
  };
}

/**
 * The run that owns this record, and how we know — namespace, declared, deed.
 *
 * **The order is the whole rule.** Both enforced rules are tried before the
 * index, because an enforced answer must never lose to an observed one: an
 * agent's namespace still wins over any stray record naming the same bot. The
 * `controllerId` fallback sits between them and the deeds for the same reason —
 * a standalone executor's `agent_id` tag is enforced too (`risk.py`).
 *
 * The deed lookup is last and cheapest: one object lookup per name in the
 * bot's deploy chain (see {@link deployNameChain}), and a chain is two names long
 * on every bot that was deployed once.
 *
 * One record, one index: a caller with a loop wants {@link attributionIndex}
 * instead, which is where the rule now lives.
 */
export function attributionOf(
  owners: FleetOwner[],
  deeds: DeedIndex | null | undefined,
  botName: string,
  controllerId: string = "",
): Attribution {
  return attributionIndex(owners, deeds)(botName, controllerId);
}

/**
 * What an agent row reports about its own evidence: `deed` only when every leaf
 * under it agrees, so a mixed row never claims to be softer than it is.
 *
 * Structurally typed over `how` rather than over `PerfLeaf`, to keep this module
 * free of the tree it feeds (the ARCH-300 split, and the reason every rule here
 * is reachable from a test).
 */
export function provenanceOf(leaves: readonly { how?: Provenance }[]): Provenance {
  if (leaves.length === 0) return "none";
  const first = leaves[0].how ?? "none";
  return leaves.every((leaf) => (leaf.how ?? "none") === first) ? first : "namespace";
}

/** The owner a run key names, or `undefined` when the map does not know it. */
export function ownerOf(
  owners: readonly FleetOwner[],
  runKey: string,
): FleetOwner | undefined {
  return owners.find((owner) => owner.runKey === runKey);
}

/**
 * The three pseudo-strategies, in the order they became possible.
 *
 * `condor/agents/deeds.py`'s `RESERVED_STRATEGY_SLUGS`, mirrored — a chat, a
 * delegation and the dashboard are runs without a strategy, but a run key needs
 * two halves, so each gets a reserved slug of its own. They are named apart
 * rather than lumped into one "not-a-loop" bucket for the reason Python names
 * them apart: *"the chat deployed it"* and *"somebody pressed Deploy"* are
 * different answers to the same question.
 *
 * A user-created strategy may not take these slugs, so a run key ending in one
 * is a pseudo-run with certainty rather than by convention. That certainty is
 * why this can stay a list of **slugs**: the *words* for a pseudo-run are not
 * here and must not be, because they already ship on the wire — see
 * {@link ownerRowLabel}.
 */
export const PSEUDO_STRATEGIES = ["chat", "delegation", "ui"] as const;

const PSEUDO_SLUGS: ReadonlySet<string> = new Set<string>(PSEUDO_STRATEGIES);

/** `"brigado.brl_mm"` → `{ agent: "brigado", strategy: "brl_mm" }`. */
export function splitRunKey(runKey: string): { agent: string; strategy: string } {
  const dot = runKey.indexOf(".");
  return dot < 0
    ? { agent: runKey, strategy: "" }
    : { agent: runKey.slice(0, dot), strategy: runKey.slice(dot + 1) };
}

/** Whether a run key names a chat, a delegation or the dashboard. */
export function isPseudoRunKey(runKey: string): boolean {
  return PSEUDO_SLUGS.has(splitRunKey(runKey).strategy);
}

/**
 * What a run key is called on screen: `"brigado.brl_mm"` → `"brigado / brl_mm"`.
 *
 * The **slugs**, not the display names, and deliberately: the rows beneath an
 * agent are bot names built out of exactly these two slugs
 * (`brigado-brl_mm-btc-20260731-101500`), so the slug form is the one a reader
 * can match by eye down the column. It is also the id in the URL, so the row
 * and the link it copies say the same thing.
 *
 * Pure over the key, which is what lets the scope tree label an agent row
 * without the fleet map in hand: the node id already carries the answer.
 */
export function runKeyLabel(runKey: string): string {
  const dot = runKey.indexOf(".");
  return dot < 0 ? runKey : `${runKey.slice(0, dot)} / ${runKey.slice(dot + 1)}`;
}

/**
 * The same subject spelled out: `"Brigado / BRL MM"`.
 *
 * What the map's display names are for — the tooltip on a label that is an id.
 * Falls back to the label itself for an owner the map no longer holds, so a
 * stale deep link still names something rather than nothing.
 */
export function ownerTitle(owners: readonly FleetOwner[], runKey: string): string {
  const owner = ownerOf(owners, runKey);
  if (!owner) return runKeyLabel(runKey);
  return `${owner.agentName || owner.agentSlug} / ${owner.strategyName || owner.strategySlug}`;
}

/**
 * **The one place a run key's owner row is named**: slugs for a strategy, the
 * fleet map's words for a pseudo-run.
 *
 * {@link runKeyLabel}'s reason for preferring slugs is precise, and for a real
 * strategy it is right — the rows beneath the agent are bot names built out of
 * exactly those two slugs, so the slug form is the one a reader matches by eye.
 * **That rationale cannot reach a pseudo-run.** A pseudo-owner is built with an
 * empty namespace and the emptiness is load-bearing
 * (`condor/agents/fleet_map.py:_pseudo_owners`): `inNamespace` refuses an empty
 * namespace in both languages, so a `condor-ui-…` bot name can never exist to
 * be matched against. `ui` is then a slug naming nothing the reader has ever
 * seen, and *the dashboard* and *the chat* read as two unrelated systems rather
 * than two doors of the same one.
 *
 * The words are **not** minted here, and there is deliberately no TS copy of
 * `PSEUDO_STRATEGY_NAMES`: `condor/agents/deed_index.py` maps `ui → "Dashboard"`,
 * `chat → "Chat"` and `delegation → "Delegation"`, and the map already ships
 * them as every pseudo-owner's `strategyName`. A second copy in the browser
 * would be exactly the Python/TS drift this chain suffers from elsewhere.
 *
 * An owner the map no longer holds falls back to slugs through
 * {@link ownerTitle}, so a stale deep link names something rather than nothing.
 *
 * A caller that also has the two unowned buckets in hand wants
 * `agentBucketLabel` (`components/perf/agentFilter`), which is this plus those
 * two fixed labels — and nothing else names an owner row.
 */
export function ownerRowLabel(owners: readonly FleetOwner[], runKey: string): string {
  return isPseudoRunKey(runKey) ? ownerTitle(owners, runKey) : runKeyLabel(runKey);
}


// ── What the loop is doing ──

/** `38s`, `2m 05s`, `1h 04m` — the shape that reads at a glance at every scale. */
export function countdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s >= 3600) {
    return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  }
  if (s >= 60) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${s}s`;
}

/** The word the status dot and the header read: a loop, or the absence of one. */
export function loopStatus(live: LiveLoop | null | undefined): string {
  return live?.status || "idle";
}

/**
 * The header band's middle line: session, tick, and when the next one is due.
 *
 * Pure over the loop and the clock so the two judgement calls in it are
 * testable rather than only observable. They are: a loop that has not ticked
 * yet has no countdown to show (its `lastTickAt` is 0, and 0 + frequency is
 * 1970), and a tick that is running long is reported as **overdue** — the
 * negative number it would otherwise print reads as a bug in the page rather
 * than as the real state of a slow tick.
 */
export function loopFacts(live: LiveLoop | null | undefined, nowMs: number): string[] {
  if (!live) return [];
  const facts = [`session ${live.sessionNum}`, `tick ${live.tickCount}`];
  if (live.status !== "running") return facts;
  if (live.lastTickAt <= 0) {
    facts.push("first tick pending");
    return facts;
  }
  const dueIn = live.lastTickAt + live.frequencySec - nowMs / 1000;
  facts.push(dueIn > 0 ? `next tick ${countdown(dueIn)}` : `tick overdue by ${countdown(-dueIn)}`);
  return facts;
}
