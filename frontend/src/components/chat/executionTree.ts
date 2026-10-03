// ── The fleet, read agent-first, as rows (FEAT-114) ──
//
// The Execution panel used to group a server's controllers by the bot that
// deployed them and stop there, and `/fleet` answered *"what is every agent
// doing"* on a page of its own. Two surfaces for one fleet is two folds of one
// set of records, which is the disagreement ARCH-324 exists to prevent — so the
// page is gone and its question is a level in this panel's tree instead.
//
// The level itself is not new: `buildTree(leaves, "All", { grouping: ["agent",
// "bot"] })` is the same call `/bots` makes, and `keyFor(leaf, "agent")` never
// returns `""` — an unowned leaf is bucketed into *No record found* or *Before
// the ledger* rather than dropped. So the agent rows partition the panel's
// total by construction, which is the property the fold discipline rests on.
//
// **An agent's money is everything it ever deployed.** The live rows are what
// is trading now, but an agent row's totals also carry the bots it stopped and
// the executors that closed — the same lifetime figure the race monitor posts
// (`scripts/hackathon_monitor.py`) — under one `history` row per agent, so the
// rows beneath an agent still add up to it. Stopping a bot moves its money from
// a controller row to the history row; it never takes it off the agent.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split), so every
// judgement below — which rows exist, which level collapses, which agent a run
// key belongs to — is reachable from a test rather than only from a rendered
// panel.

import { agentBucketLabel } from "@/components/perf/agentFilter";
import {
  isPseudoRunKey,
  splitRunKey,
  type DeedIndex,
  type FleetOwner,
} from "@/lib/agent-attribution";
import type { AgentSummary } from "@/lib/api";
import { distinguishes } from "@/lib/perf-grouping";
import {
  AXIS_PREFIX,
  buildTree,
  foldLeaves,
  type ConvertQuote,
  type PerfLeaf,
  type PerfNode,
  type PerfTotals,
} from "@/lib/perf-tree";

/** The nesting this panel reads the fleet in — `/bots`' own default. */
const GROUPING = ["agent", "bot"] as const;

/**
 * How small a fleet has to be for its agent rows to be open on arrival.
 *
 * Three levels in a 300px column is the panel's real risk, so the rows that
 * cost the most depth are the ones that have to earn being open. A handful of
 * agents fits; a dozen would bury the controllers under a page of headers, and
 * a reader with that many is looking for one of them rather than reading all.
 */
export const AUTO_OPEN_AGENTS = 3;

/**
 * One line of the panel — an agent, a bot, a controller, or what an agent has
 * already finished.
 *
 * Executors are deliberately not rows: they are counted on the controller that
 * is running them, exactly as they were before this feature, and a third
 * expandable level in this column would be depth nobody can read. They are
 * still in every `leaves` above them, so nothing is missing from a total.
 */
export interface ExecutionRow {
  /**
   * `agent:{runKey}` / `bot:{name}` / a controller node id — the browser's own
   * ids — or `{agent id}::history` for the finished records under an agent.
   */
  id: string;
  kind: "agent" | "bot" | "controller" | "history";
  label: string;
  depth: 0 | 1 | 2;
  /** The row this one hangs under, `null` at the top — what {@link visibleRows} walks. */
  parentId: string | null;
  /** Whether anything hangs under it, so a chevron is only drawn where one opens something. */
  hasChildren: boolean;
  /** `foldLeaves` over this node's spine, in display currency. */
  totals: PerfTotals;
  /** The accounting spine, for the executor count and the controller's own record. */
  leaves: PerfLeaf[];
  /** Present on `agent` rows the fleet map claims — the agent behind the run key. */
  agent?: { slug: string; name: string };
}

export interface ExecutionInput {
  /** The running population — what is trading now, and what draws a row. */
  leaves: readonly PerfLeaf[];
  /**
   * The terminated population (`terminatedLeaves`): stopped bots' controllers
   * and closed executors. Only what is credited to a real agent is kept — see
   * {@link agentHistory} — and it is folded into that agent's row, never drawn
   * as rows of its own.
   */
  history?: readonly PerfLeaf[];
  deeds: DeedIndex | null;
  /** `["agents"]`, for turning a run key into an agent somebody can open. */
  agents: readonly AgentSummary[];
  /**
   * The fleet map, for naming a pseudo-run's row.
   *
   * A chat, a delegation and the dashboard have no strategy to spell out, and
   * the words for them ship on the wire rather than living in the browser —
   * `ownerRowLabel` is where that is decided, and it needs the map.
   */
  owners: readonly FleetOwner[];
  convert: ConvertQuote;
  /** The clock a fold measures a runtime against. */
  now: number;
}

const AGENT_PREFIX = AXIS_PREFIX.agent;

/** The suffix a history row's id carries after its agent's id. */
export const HISTORY_SUFFIX = "::history";

/**
 * The finished records an agent row is credited with.
 *
 * **Only an agent's.** A pseudo-run (a chat, a delegation, the dashboard) and an
 * unowned record keep their live rows and nothing else: the terminated side of
 * a real server holds every hand-opened position it has ever had, and this
 * panel is read for what the agents did, not for the archive.
 *
 * **A stopped controller counts what it realized, not its last mark.** Its
 * final snapshot outlives the bot, so the unrealized PnL in it is a frozen mark
 * on a book nobody is quoting any more, not money ([[CORR-633]]). This is the
 * rule the server-side aggregator (`_merge_stopped_instance`) applies, and so
 * the race monitor, so the dock and the race board cannot disagree about a
 * stopped bot. A closed executor's PnL is already all realized.
 */
export function agentHistory(history: readonly PerfLeaf[]): PerfLeaf[] {
  const out: PerfLeaf[] = [];
  for (const leaf of history) {
    if (!leaf.agent || isPseudoRunKey(leaf.agent)) continue;
    out.push(
      leaf.kind === "controller"
        ? { ...leaf, unrealized: 0, net: leaf.realized, positions: [] }
        : leaf,
    );
  }
  return out;
}

/**
 * Every row the panel can draw, parents before their children.
 *
 * The whole tree rather than only what is open, because *which* rows default to
 * open is a question about the tree's shape — a fleet of two agents opens them
 * both, a fleet of twenty opens none — and a producer that had already dropped
 * the closed rows could not be asked. {@link openRows} answers it and
 * {@link visibleRows} does the trimming, and both are as testable as this is.
 */
export function executionRows(input: ExecutionInput): ExecutionRow[] {
  const { leaves, deeds, agents, owners, convert, now } = input;
  const history = agentHistory(input.history ?? []);
  // One tree over both populations, live first so live agents keep the top of
  // the panel. `buildTree`'s spine rule is what keeps this from double
  // counting: a closed executor of a live controller hangs under that
  // controller's node, whose spine is the controller record — which already
  // covers every executor it ever ran.
  const tree = buildTree([...leaves, ...history], "All", { grouping: [...GROUPING], deeds });
  const bySlug = new Map(agents.map((agent) => [agent.slug, agent]));
  const live = new Set(leaves);
  const isLive = (leaf: PerfLeaf) => live.has(leaf);
  const foldOf = (spine: PerfLeaf[]) => foldLeaves(spine, convert, now);
  const fold = (node: PerfNode) => foldOf(node.leaves.filter(isLive));

  /**
   * A controller node, as a row — or nothing.
   *
   * A controller node whose spine is not a controller record cannot happen in
   * the live population (an executor whose controller is gone carries no
   * `controllerId` and hangs under a bucket instead), and if it ever did, a row
   * with no record behind it would be a line of dashes. Skipped rather than
   * drawn empty.
   */
  const controllerRow = (
    node: PerfNode,
    depth: 1 | 2,
    parentId: string,
  ): ExecutionRow | null => {
    const leaf = node.leaves[0];
    // A finished controller is in the tree only to be folded into its agent;
    // its row would be a controller nothing is running.
    if (!leaf || leaf.kind !== "controller" || !isLive(leaf)) return null;
    return {
      id: node.id,
      kind: "controller",
      label: leaf.label,
      depth,
      parentId,
      hasChildren: false,
      totals: fold(node),
      leaves: node.leaves,
    };
  };

  const rows: ExecutionRow[] = [];

  for (const agentNode of tree.children) {
    // Every root child is an owner row: the agent axis buckets what it cannot
    // credit rather than skipping it, which is what makes these rows a
    // partition of the panel's total instead of a selection from it.
    if (agentNode.kind !== "agent") continue;

    const key = agentNode.id.slice(AGENT_PREFIX.length);
    const { agent: slug, strategy } = splitRunKey(key);
    const claimed = bySlug.get(slug);

    // The bot level collapses when it tells this agent's records nothing apart
    // — the sidebar's own rule, per owner rather than per fleet: a fleet
    // running one bot must not spend a chevron saying so, and neither must an
    // agent that does.
    const liveSpine = agentNode.leaves.filter(isLive);
    const finished = agentNode.leaves.filter((leaf) => !isLive(leaf));
    const botLevel = distinguishes(liveSpine, "bot", deeds);

    const under: ExecutionRow[] = [];
    for (const child of agentNode.children) {
      if (child.kind === "controller") {
        const row = controllerRow(child, 1, agentNode.id);
        if (row) under.push(row);
        continue;
      }
      if (child.kind !== "bot") continue;
      // A bot that only has finished records under it is history, not a row.
      if (!child.leaves.some(isLive)) continue;
      const controllers = child.children
        .map((c) => controllerRow(c, botLevel ? 2 : 1, botLevel ? child.id : agentNode.id))
        .filter((row): row is ExecutionRow => row !== null);
      if (!botLevel) {
        under.push(...controllers);
        continue;
      }
      under.push({
        id: child.id,
        kind: "bot",
        label: child.label,
        depth: 1,
        parentId: agentNode.id,
        hasChildren: controllers.length > 0,
        totals: fold(child),
        leaves: child.leaves.filter(isLive),
      });
      under.push(...controllers);
    }

    if (finished.length > 0) {
      under.push({
        id: `${agentNode.id}${HISTORY_SUFFIX}`,
        kind: "history",
        label: historyLabel(finished),
        depth: 1,
        parentId: agentNode.id,
        hasChildren: false,
        totals: foldOf(finished),
        leaves: finished,
      });
    }

    rows.push({
      id: agentNode.id,
      kind: "agent",
      // A real strategy is said as the agent's name over the strategy's slug:
      // the bot rows beneath it are built out of that slug, so it is the half a
      // reader matches by eye. A pseudo-run has no such bot name — and no
      // strategy — so it is named the one way an owner row is ever named
      // elsewhere, and so is an attributed run key no listed agent claims: the
      // residual stays named rather than swept away.
      label:
        claimed && strategy && !isPseudoRunKey(key)
          ? `${claimed.name} / ${strategy}`
          : agentBucketLabel(key, owners),
      depth: 0,
      parentId: null,
      hasChildren: under.length > 0,
      // The whole spine, finished records included: what this agent has made.
      totals: foldOf(agentNode.leaves),
      leaves: agentNode.leaves,
      ...(claimed ? { agent: { slug: claimed.slug, name: claimed.name } } : {}),
    });
    rows.push(...under);
  }

  return rows;
}

/**
 * What a history row says it holds: `"History · 2 stopped bots, 14 closed executors"`.
 *
 * Bots rather than controllers, because a stop is something done to a bot — and
 * the executors counted are the ones filed under no finished controller (the
 * agent's own standalone ones), since a controller's record already covers its.
 */
export function historyLabel(finished: readonly PerfLeaf[]): string {
  const bots = new Set<string>();
  let executors = 0;
  for (const leaf of finished) {
    if (leaf.kind === "controller") bots.add(leaf.bot);
    else executors += 1;
  }
  const parts: string[] = [];
  if (bots.size) parts.push(`${bots.size} stopped bot${bots.size === 1 ? "" : "s"}`);
  if (executors) parts.push(`${executors} closed executor${executors === 1 ? "" : "s"}`);
  return `History · ${parts.join(", ")}`;
}

/**
 * Which rows are expanded: the default for this fleet's shape, with the
 * reader's own toggles on top.
 *
 * `toggled` holds only what somebody clicked, so the default can change under
 * them — an agent row that arrives while the fleet is small is open, and the
 * one they shut stays shut whatever the fleet does next.
 */
export function openRows(
  rows: readonly ExecutionRow[],
  toggled: Readonly<Record<string, boolean>>,
): Set<string> {
  const agents = rows.reduce((n, row) => n + (row.kind === "agent" ? 1 : 0), 0);
  const open = new Set<string>();
  for (const row of rows) {
    if (!row.hasChildren) continue;
    // Bots default open for the reason they always have in the sidebar: a bot
    // row is opened to reach the controllers under it, which is what the
    // reader came for. Agents are the level that can bury them, so they are
    // the level that has to earn it.
    const byDefault = row.kind === "agent" ? agents <= AUTO_OPEN_AGENTS : true;
    if (toggled[row.id] ?? byDefault) open.add(row.id);
  }
  return open;
}

/** The rows under an open ancestor — parents come first, so one pass does it. */
export function visibleRows(
  rows: readonly ExecutionRow[],
  open: ReadonlySet<string>,
): ExecutionRow[] {
  const shown = new Set<string>();
  const out: ExecutionRow[] = [];
  for (const row of rows) {
    if (row.parentId !== null && !(shown.has(row.parentId) && open.has(row.parentId))) {
      continue;
    }
    shown.add(row.id);
    out.push(row);
  }
  return out;
}

/** What the panel's header counts, over the whole tree rather than what is open. */
export function executionCounts(rows: readonly ExecutionRow[]): {
  controllers: number;
  paused: number;
} {
  let controllers = 0;
  let paused = 0;
  for (const row of rows) {
    if (row.kind !== "controller") continue;
    controllers += 1;
    if (row.leaves[0]?.status === "stopped") paused += 1;
  }
  return { controllers, paused };
}
