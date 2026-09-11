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
// returns `""` — an unowned leaf is bucketed into *Outside Condor* or *Before
// the ledger* rather than dropped. So the agent rows partition the panel's
// total by construction, which is the property the fold discipline rests on.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split), so every
// judgement below — which rows exist, which level collapses, which agent a run
// key belongs to — is reachable from a test rather than only from a rendered
// panel.

import { splitRunKey } from "@/components/agent/workspace/reconcile";
import { agentBucketLabel } from "@/components/perf/agentFilter";
import type { DeedIndex } from "@/lib/agent-attribution";
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
 * One line of the panel — an agent, a bot, or a controller.
 *
 * Executors are deliberately not rows: they are counted on the controller that
 * is running them, exactly as they were before this feature, and a third
 * expandable level in this column would be depth nobody can read. They are
 * still in every `leaves` above them, so nothing is missing from a total.
 */
export interface ExecutionRow {
  /** `agent:{runKey}` / `bot:{name}` / a controller node id — the browser's own ids. */
  id: string;
  kind: "agent" | "bot" | "controller";
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
  leaves: readonly PerfLeaf[];
  deeds: DeedIndex | null;
  /** `["agents"]`, for turning a run key into an agent somebody can open. */
  agents: readonly AgentSummary[];
  convert: ConvertQuote;
  /** The clock a fold measures a runtime against. */
  now: number;
}

const AGENT_PREFIX = AXIS_PREFIX.agent;

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
  const { leaves, deeds, agents, convert, now } = input;
  const tree = buildTree([...leaves], "All", { grouping: [...GROUPING], deeds });
  const bySlug = new Map(agents.map((agent) => [agent.slug, agent]));
  const fold = (node: PerfNode) => foldLeaves(node.leaves, convert, now);

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
    if (!leaf || leaf.kind !== "controller") return null;
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
    const botLevel = distinguishes(agentNode.leaves, "bot", deeds);

    const under: ExecutionRow[] = [];
    for (const child of agentNode.children) {
      if (child.kind === "controller") {
        const row = controllerRow(child, 1, agentNode.id);
        if (row) under.push(row);
        continue;
      }
      if (child.kind !== "bot") continue;
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
        leaves: child.leaves,
      });
      under.push(...controllers);
    }

    rows.push({
      id: agentNode.id,
      kind: "agent",
      // An attributed run key no listed agent claims keeps the label the key
      // itself carries: the residual stays named rather than swept away.
      label: claimed && strategy ? `${claimed.name} / ${strategy}` : agentBucketLabel(key),
      depth: 0,
      parentId: null,
      hasChildren: under.length > 0,
      totals: fold(agentNode),
      leaves: agentNode.leaves,
      ...(claimed ? { agent: { slug: claimed.slug, name: claimed.name } } : {}),
    });
    rows.push(...under);
  }

  return rows;
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
