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
  const name = stripDeploySuffix((botName || "").trim());
  if (!name) return "";
  const byLength = [...owners].sort((a, b) => b.namespace.length - a.namespace.length);
  for (const owner of byLength) {
    if (inNamespace(name, owner.namespace)) return owner.runKey;
  }
  for (const owner of byLength) {
    if (owner.declaredBots.some((declared) => inNamespace(name, declared))) {
      return owner.runKey;
    }
  }
  return "";
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

/** The owner a run key names, or `undefined` when the map does not know it. */
export function ownerOf(owners: FleetOwner[], runKey: string): FleetOwner | undefined {
  return owners.find((owner) => owner.runKey === runKey);
}

/**
 * What a run key is called on screen: `"Brigado / BRL MM"`.
 *
 * The display names, which is why the map carries them beside the slugs — the
 * slugs are the *id*, and an id is what the URL wants, not what a row wants.
 * Falls back to the run key's own halves for an owner the map no longer holds,
 * so a stale deep link still names something.
 */
export function ownerLabel(owners: FleetOwner[], runKey: string): string {
  const owner = ownerOf(owners, runKey);
  if (owner) {
    return `${owner.agentName || owner.agentSlug} / ${owner.strategyName || owner.strategySlug}`;
  }
  const dot = runKey.indexOf(".");
  return dot < 0 ? runKey : `${runKey.slice(0, dot)} / ${runKey.slice(dot + 1)}`;
}
