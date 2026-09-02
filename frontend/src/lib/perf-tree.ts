// ── One leaf, one fold, one tree (FEAT-086) ──
//
// Controllers and executors are the same report at two granularities — volume,
// realized/unrealized PnL, close types, win rate, runtime — and the dashboard
// used to tell that story twice, on two pages, in two idioms. This module is
// the vocabulary they share: every record the browser can report on becomes a
// `PerfLeaf`, every node of the scope sidebar is a `PerfNode` over those
// leaves, and every number on screen comes out of the one `foldLeaves`.
//
// Nothing here renders, fetches or converts. `foldLeaves` takes the conversion
// as an argument for the same reason the components pass it down: the display
// currency is a preference, and a fold that reached for it would be untestable.

import type { BotRunInfo, ControllerInfo, ExecutorInfo } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { isExecutorActive, toMs } from "@/lib/formatters";

/** Which set of records is in scope: what is live, or what has finished. */
export type Population = "running" | "terminated";

/**
 * The view lives in the URL, so a population and a scope together are a link —
 * and the chat can report what is actually on screen (FEAT-060).
 *
 * Falls back to the default rather than throwing: a hand-edited or stale query
 * parameter should land the reader on the live fleet, which is the page they
 * asked for, not on an error.
 */
export function parsePopulation(raw: string | null): Population {
  return raw === "terminated" ? "terminated" : "running";
}

/**
 * The bot a leaf hangs under when nothing at all names one.
 *
 * `ExecutorInfo` carries a `controller_id` but no bot (see `condor/web/models.py`),
 * so an executor is attributed to a bot only by matching that id against a live
 * controller or a run window. The ones that do not match are real and are
 * exactly the rows you go looking for — an executor left behind by a controller
 * that is gone, a position opened by hand from `/trade` — so they are kept
 * rather than dropped on the floor, filed under the controller id they carry
 * (`main`, for a hand-opened one) as if that were their bot. This label is
 * what remains for an executor that carries no controller id either.
 */
export const UNATTACHED_BOT = "(unattached)";

/**
 * The label a leaf falls back to when the field it is bucketed by is empty.
 *
 * Exported so a caller can tell "we know this is a `pmm_simple`" from "we have
 * no name for this at all": it reads as a name in a row's caption, but it is
 * the absence of one, and a filter that offered it as a class would offer a
 * bucket nobody chose to be in.
 */
export const UNKNOWN_LABEL = "—";

/**
 * Anything the browser can report on, in one vocabulary.
 *
 * The numbers are in the leaf's own **quote**, not in display currency: the
 * conversion needs the `pair` and belongs to the caller, which is why `pair`
 * rides along beside them and why `foldLeaves` takes a converter.
 *
 * The one real difference between the two kinds is `closeTypes`: an executor
 * closed exactly once, so it is a histogram with a single entry; a controller
 * is a bag of executors, so it is a histogram of many. Folding them is then the
 * same operation at both granularities, which is the whole point.
 */
export interface PerfLeaf {
  /** Unique within a population: a controller key or an executor id. */
  id: string;
  kind: "controller" | "executor";
  /** What the sidebar and the row tables call it. */
  label: string;
  /** Which bot ran it — the sidebar's bot bubbles filter on this. */
  bot: string;
  /** Which controller it belongs to; `""` for a leaf that belongs to none. */
  controllerId: string;
  /**
   * The class of thing it is — what the sidebar's type bubbles filter on.
   *
   * A controller's is its controller type (`pmm_simple`, `grid_strike`); an
   * executor's is its executor type (`position_executor`, `grid_executor`).
   * They are deliberately the *same* field holding two different vocabularies,
   * because a controller has no executor type of its own: it has as many as its
   * executors have, so naming one would mean picking a winner among them. Which
   * vocabulary a value belongs to is told by the leaf's `kind`, which is why the
   * sidebar offers controller classes and executor types as two separate bubble
   * groups rather than one list that half the population can never match.
   */
  executorType: string;
  connector: string;
  pair: string;
  /** Quote-denominated. `net` is the leaf's own total, not `realized + unrealized`. */
  realized: number;
  unrealized: number;
  net: number;
  volume: number;
  fees: number;
  /** Declared capital in quote, or 0 for a leaf that declares none. */
  capital: number;
  /** One entry for an executor, a histogram for a controller. */
  closeTypes: Record<string, number>;
  positions: Record<string, unknown>[];
  /** Epoch ms, or null when the record does not say. */
  startedAt: number | null;
  /** Epoch ms; null while running, and also when a closed record lost its close time. */
  endedAt: number | null;
  /** Whether this leaf is still live — the thing `endedAt: null` cannot say alone. */
  running: boolean;
  status: string;
  /**
   * The leaf's own return, in percent.
   *
   * Per leaf and never folded: each is measured against its own notional, so
   * summing or averaging them across a scope reports a return nobody earned.
   * `foldLeaves` carries it through only for a fold of exactly one leaf.
   */
  returnPct?: number;
  /** The source record, for the detail panel and the row tables. */
  source: ControllerInfo | ExecutorInfo;
}

function finiteOr(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** A live controller, as the browser reports it. */
export function leafFromController(c: ControllerInfo): PerfLeaf {
  const capital = finiteOr(c.config?.total_amount_quote, 0);
  const started = c.deployed_at ? Date.parse(c.deployed_at) : NaN;
  // The kill switch is what actually stops a controller; `status` in this
  // payload is a hardcoded "running" (see the /bots route's own note).
  const killed = c.config?.manual_kill_switch === true;
  // A controller is never *finished*: this payload only ever describes live
  // ones, and a paused controller is still deployed. Reading the kill switch as
  // "finished" would end the scope's runtime at an end nothing recorded, so a
  // bot whose controllers were all paused would report no runtime at all — and
  // every per-hour pace on the strip with it. Paused is a `status`, which is
  // what the sidebar dot and the header read.
  return {
    id: controllerKey(c),
    kind: "controller",
    label: c.controller_id || c.controller_name,
    bot: c.bot_name,
    controllerId: c.controller_id || c.controller_name,
    executorType: c.controller_name || UNKNOWN_LABEL,
    connector: c.connector || "",
    pair: c.trading_pair || "",
    realized: c.realized_pnl_quote,
    unrealized: c.unrealized_pnl_quote,
    net: c.global_pnl_quote,
    volume: c.volume_traded,
    // The controllers payload reports no fee total of its own.
    fees: 0,
    capital: capital > 0 ? capital : 0,
    closeTypes: c.close_type_counts || {},
    positions: c.positions_summary || [],
    startedAt: Number.isNaN(started) ? null : started,
    endedAt: null,
    running: true,
    status: killed ? "stopped" : c.status,
    returnPct: c.global_pnl_pct,
    source: c,
  };
}

/**
 * One controller of a run that has finished.
 *
 * The same record as a live controller's, read the other way round.
 * `leafFromController` hardcodes `running: true, endedAt: null`, which is right
 * for a payload that only ever describes live controllers and wrong for one
 * that has stopped — and it is not cosmetic: `foldLeaves` measures a scope's
 * runtime to *now* while anything in it is running, so a terminated fold
 * inheriting that would grow a runtime for trading that ended last week, and
 * every per-hour pace on the strip would shrink to match.
 *
 * The run supplies the two things the controller record cannot: when it stopped,
 * and — through `deployed_at` — when it started.
 *
 * `executorType` stays unknown rather than borrowed. Upstream reports no
 * `controller_name` on these rows at all, and the run's `strategy_type` names
 * the *bot's* strategy (`controller`, `v2_with_controllers`), not the class of
 * this controller. Grouping by a value that is not the thing being grouped is
 * worse than saying so.
 */
export function leafFromTerminatedController(c: ControllerInfo, run?: BotRunInfo): PerfLeaf {
  const started = c.deployed_at ? Date.parse(c.deployed_at) : NaN;
  const stopped = run?.stopped_at ? Date.parse(run.stopped_at) : NaN;
  return {
    id: controllerKey(c),
    kind: "controller",
    label: c.controller_id || c.controller_name,
    bot: c.bot_name,
    controllerId: c.controller_id || c.controller_name,
    executorType: c.controller_name || UNKNOWN_LABEL,
    connector: c.connector || "",
    pair: c.trading_pair || "",
    realized: c.realized_pnl_quote,
    unrealized: c.unrealized_pnl_quote,
    net: c.global_pnl_quote,
    volume: c.volume_traded,
    fees: 0,
    capital: 0,
    closeTypes: c.close_type_counts || {},
    positions: c.positions_summary || [],
    startedAt: Number.isNaN(started) ? null : started,
    endedAt: Number.isNaN(stopped) ? null : stopped,
    running: false,
    status: "stopped",
    returnPct: c.global_pnl_pct,
    source: c,
  };
}

/**
 * One executor, live or archived.
 *
 * `bot` is passed in because the record does not carry one: the caller matches
 * `controller_id` against the live fleet and hands over what it found, or
 * `UNATTACHED_BOT` when nothing matched. An executor nobody claims is then
 * filed under its own `controller_id` as its bot, and under no controller: the
 * id is the one name the record carries, and it names the executor rather than
 * a controller — `main` is Condor's default for a position opened by hand, and
 * there is no controller behind it for a row of the tree to stand for. So the
 * bot bubbles offer `main`, and the executor hangs off the fleet directly
 * instead of under a controller row that told the reader nothing the row
 * beneath it did not.
 *
 * An executor reports a single `pnl` and no split. While it is open that figure
 * is unrealised — the position is still on the book — and once it has closed it
 * is realised. Splitting it that way rather than charging all of it to one
 * column is what lets a controller scope and an executor scope be read side by
 * side without one of them lying about which half of its total is banked.
 */
export function leafFromExecutor(e: ExecutorInfo, bot: string = UNATTACHED_BOT): PerfLeaf {
  const running = isExecutorActive(e.status);
  const closedAt = e.close_timestamp > 0 ? toMs(e.close_timestamp) : null;
  const attached = !!bot && bot !== UNATTACHED_BOT;
  return {
    id: e.id,
    kind: "executor",
    label: e.id,
    bot: attached ? bot : e.controller_id || UNATTACHED_BOT,
    controllerId: attached ? e.controller_id || "" : "",
    executorType: e.type || UNKNOWN_LABEL,
    connector: e.connector || "",
    pair: e.trading_pair || "",
    realized: running ? 0 : e.pnl,
    unrealized: running ? e.pnl : 0,
    net: e.pnl,
    volume: e.volume,
    fees: e.cum_fees_quote,
    capital: 0,
    closeTypes: e.close_type ? { [e.close_type]: 1 } : {},
    positions: [],
    startedAt: e.timestamp > 0 ? toMs(e.timestamp) : null,
    endedAt: running ? null : closedAt,
    running,
    status: e.status,
    // `net_pnl_pct` is a fraction on the wire; the strip shows percent.
    returnPct: e.net_pnl_pct ? e.net_pnl_pct * 100 : undefined,
    source: e,
  };
}

/**
 * What a run's state actually is, in the word the dashboard shows.
 *
 * Not `run_status`, which is the field the question would seem to be about:
 * upstream never writes `RUNNING`, and every bot trading right now reports the
 * literal string `CREATED` — so the status dot read "created" for a live fleet
 * and "stopped" was the only value that ever meant what it said. `is_live` is
 * derived from the deployment (see `BotRunInfo`), and a run that has a stop
 * time has stopped whatever the column says.
 */
export function runStatus(r: BotRunInfo): string {
  if (r.is_live) return "running";
  if (r.stopped_at) return "stopped";
  return (r.run_status || "").toLowerCase();
}

/**
 * The controller class a leaf belongs to, or `""` when it belongs to none.
 *
 * The one rule behind the browser's "Controller type" filter, and it is
 * narrower than it looks. A controller is its own class. An executor *inherits*
 * the class of the controller that ran it — it carries none of its own, so
 * `classById` (config id → `controller_name`) is what answers for it. Anything
 * else has no class at all:
 *
 * - a controller-less executor (a position opened by hand from `/trade`, filed
 *   under its own controller id as its bot) has nothing to inherit. Standing
 *   its *executor* type in as a class put `grid` and `order` in the controller
 *   row and in the executor-type row at once, counted identically, so the
 *   sidebar asked one question twice;
 * - a controller whose class upstream never reported carries
 *   {@link UNKNOWN_LABEL}, which is a dash where a name would go rather than a
 *   name. Offering it as a class made a bubble that narrowed to "the records we
 *   know nothing about" — on a real server, all 139 finished controllers, since
 *   `controller-performance` reports no class on a finished run at all.
 *
 * Returning `""` for both is what keeps them out of the options *and* out of
 * the counts, since the tally that builds the bubbles skips empty keys.
 */
export function controllerClassOf(leaf: PerfLeaf, classById: Map<string, string>): string {
  if (leaf.kind === "controller") {
    return leaf.executorType === UNKNOWN_LABEL ? "" : leaf.executorType;
  }
  return leaf.controllerId ? classById.get(leaf.controllerId) ?? "" : "";
}

// ── The tree ──

export type NodeKind = "fleet" | "controller" | "executor";

export interface PerfNode {
  /** `all` | `ctrl:k` | `exec:id` */
  id: string;
  kind: NodeKind;
  label: string;
  /**
   * The leaves this node's numbers are folded from — its **accounting spine**,
   * which is deliberately *not* "every leaf beneath it".
   *
   * A controller that has both a controller leaf and executor children is
   * covered by the controller leaf alone: the controller record is the
   * authoritative one (it includes executors that closed long ago and were
   * never loaded), and adding its children's leaves to it would count the same
   * trading twice. The rule that follows is the one `buildTree` applies
   * everywhere: **a node folds its own leaf when it has one, and its children's
   * spines when it does not.**
   */
  leaves: PerfLeaf[];
  children: PerfNode[];
}

/** The node id a leaf is reported under when it is selected on its own. */
export function leafNodeId(leaf: PerfLeaf): string {
  return leaf.kind === "controller" ? `ctrl:${leaf.id}` : `exec:${leaf.id}`;
}

/** The node id of the controller a leaf belongs to, or `null` for none. */
export function controllerNodeId(leaf: PerfLeaf): string | null {
  if (!leaf.controllerId) return null;
  return `ctrl:${leaf.bot}:${leaf.controllerId}`;
}

function makeNode(id: string, kind: NodeKind, label: string): PerfNode {
  return { id, kind, label, leaves: [], children: [] };
}

/**
 * Build the scope tree over a population's leaves.
 *
 * The shape is fleet → controller → executor, and those are the only three
 * levels there are. **One shape, both populations**: a finished run's
 * controllers sit in the list exactly as a live bot's do, which is what lets
 * one sidebar, one fold and one set of panes describe a controller whether it
 * is trading or over (FEAT-089).
 *
 * There used to be a grouping level between the fleet and its controllers —
 * bot, or class of thing — chosen by a `By bot / By type` toggle. It is gone,
 * and *not* because grouping stopped being useful: a real fleet puts a dozen
 * controllers under one bot and a hundred finished runs under a period, so
 * every controller the reader actually wanted sat two chevrons deep behind a
 * row that told them nothing they did not already know. Bot and class are
 * **filters** now, drawn as bubbles above this tree, and narrowing the leaf set
 * narrows the tree — which is the same answer the grouping gave, one click
 * shallower and combinable (several bots at once, a class across bots).
 *
 * An executor whose controller is gone still hangs directly off the fleet,
 * where it used to hang off an `(unattached)` bot node: it is a real record,
 * and one the reader goes looking for.
 */
export function buildTree(leaves: PerfLeaf[], rootLabel = "All"): PerfNode {
  const fleet = makeNode("all", "fleet", rootLabel);
  const controllers = new Map<string, PerfNode>();

  // Insertion order is the caller's order, which is the order the sidebar
  // draws — the page sorts its controllers before handing them over.
  const controllerFor = (leaf: PerfLeaf): PerfNode | null => {
    const id = controllerNodeId(leaf);
    if (!id) return null;
    let node = controllers.get(id);
    if (!node) {
      node = makeNode(id, "controller", leaf.controllerId);
      controllers.set(id, node);
      fleet.children.push(node);
    }
    return node;
  };

  for (const leaf of leaves) {
    if (leaf.kind === "controller") {
      // The controller node *is* this leaf; it may already exist because one of
      // its executors was seen first, in which case it only needs its spine.
      const node = controllerFor(leaf);
      if (node) {
        node.leaves = [leaf];
        node.label = leaf.label;
      }
      continue;
    }
    const node = makeNode(leafNodeId(leaf), "executor", leaf.label);
    node.leaves = [leaf];
    const parent = controllerFor(leaf) ?? fleet;
    parent.children.push(node);
  }

  // Fold the spines upward, innermost first. A node that carries its own leaf
  // keeps it; one that does not inherits its children's.
  const settle = (node: PerfNode): PerfLeaf[] => {
    const fromChildren = node.children.flatMap(settle);
    if (node.leaves.length === 0) node.leaves = fromChildren;
    return node.leaves;
  };
  settle(fleet);

  return fleet;
}

/**
 * Every leaf in a node's whole subtree, of one kind.
 *
 * Deliberately *not* `node.leaves`, which is the accounting spine: a live
 * controller's spine is the controller record, and the executors under it are
 * exactly what this returns. The two answer different questions — "what does
 * this node add up to" and "what is underneath it" — and the rows band asks the
 * second one while the strip above it asks the first.
 */
export function collectLeaves(node: PerfNode, kind: PerfLeaf["kind"]): PerfLeaf[] {
  const found: PerfLeaf[] = [];
  const seen = new Set<string>();
  const walk = (n: PerfNode) => {
    for (const leaf of n.leaves) {
      if (leaf.kind === kind && !seen.has(leaf.id)) {
        seen.add(leaf.id);
        found.push(leaf);
      }
    }
    n.children.forEach(walk);
  };
  walk(node);
  return found;
}

/** Every node of a tree, keyed by id — the sidebar's and the scope's index. */
export function indexTree(root: PerfNode): Map<string, PerfNode> {
  const index = new Map<string, PerfNode>();
  const walk = (node: PerfNode) => {
    index.set(node.id, node);
    node.children.forEach(walk);
  };
  walk(root);
  return index;
}

/**
 * The path from a node up to the root, nearest first.
 *
 * Walks the tree, so it only answers for a node that is actually in it. The
 * re-aim after a switch uses `resolveScope` below instead, which does not need
 * the tree the node used to be in.
 */
export function ancestorChain(root: PerfNode, id: string): string[] {
  const path: string[] = [];
  const walk = (node: PerfNode): boolean => {
    path.push(node.id);
    if (node.id === id) return true;
    for (const child of node.children) {
      if (walk(child)) return true;
    }
    path.pop();
    return false;
  };
  if (!walk(root)) return [];
  return path.reverse();
}

/**
 * Every node id in the order the tree draws them, skipping what is shut.
 *
 * Openness is an allow-list rather than a deny-list, and that is the shape the
 * flat tree needs: a fleet of fourteen controllers holding a hundred and
 * nineteen executors would otherwise draw all hundred and nineteen on arrival,
 * and the reader would have to shut every controller to see the list they came
 * for. Shut is the default for a node that has never been opened; the caller
 * seeds the root.
 */
export function visibleNodeIds(root: PerfNode, open: ReadonlySet<string>): string[] {
  const ids: string[] = [];
  const walk = (node: PerfNode) => {
    ids.push(node.id);
    if (!open.has(node.id)) return;
    node.children.forEach(walk);
  };
  walk(root);
  return ids;
}

/**
 * Where a scope could fall back to, nearest first, without consulting the tree
 * it used to live in.
 *
 * Switching population or changing a filter rebuilds the tree, and a selection
 * the new one does not contain has to land *somewhere*. Resetting to the fleet
 * throws the reader's place away, and remembering the previous tree's path
 * would mean carrying a copy of the old tree through every render.
 *
 * Neither is needed, because a node id already says where it hangs: a
 * controller id names its bot and its config. The one id that says nothing on
 * its own is an executor's — deliberately, so that the *same* executor keeps
 * the *same* id whether it is live or archived, which is what lets the common
 * case need no fallback at all. Its leaf is passed in when one is known and
 * supplies the controller it hung under.
 *
 * A candidate is never *deeper* than the scope it replaces — a controller scope
 * that fell through must not land on one of its own executors, which is a
 * different (and much narrower) report than the one the reader had open. A
 * stale `bot:` or `type:` id from a link written before the grouping level was
 * retired reads as depth-2 here and so falls all the way back to the fleet,
 * which is the report it named.
 */
export function fallbackChain(scopeId: string, leaf?: PerfLeaf): string[] {
  const chain = [scopeId];
  if (leaf) {
    const ctrl = controllerNodeId(leaf);
    if (ctrl) chain.push(ctrl);
  }
  chain.push("all");

  const depth = nodeDepth(scopeId);
  const seen = new Set<string>();
  return chain.filter(
    (id) => nodeDepth(id) <= depth && !seen.has(id) && (seen.add(id), true),
  );
}

/**
 * How far down the tree an id sits, read off the id alone.
 *
 * An id that names no level of this tree — a retired `bot:`/`type:` group from
 * a link written before the grouping level went away, or plain nonsense — is
 * treated as the *shallowest* thing there is, so the only candidate that can
 * serve it is the fleet. `bot:alpha` meant "everything alpha did", and one of
 * alpha's controllers is a much narrower report than the one that link asked
 * for; the bot bubble is where that request lives now.
 */
function nodeDepth(id: string): number {
  if (id.startsWith("ctrl:")) return 1;
  if (id.startsWith("exec:")) return 2;
  return 0;
}

/**
 * The node a scope actually resolves to: itself when it still exists, and the
 * nearest surviving ancestor when it does not.
 *
 * A scope whose node has gone — a bot stopped, a config removed, a population
 * switched — would otherwise render an empty screen with no way back.
 */
export function resolveScope(
  nodes: ReadonlyMap<string, PerfNode>,
  scopeId: string,
  leaf?: PerfLeaf,
): string {
  return fallbackChain(scopeId, leaf).find((id) => nodes.has(id)) ?? "all";
}

// ── The fold ──

/** What a scope adds up to, in display currency. */
export interface PerfTotals {
  realized: number;
  unrealized: number;
  net: number;
  volume: number;
  fees: number;
  capital: number;
  /** Open positions held across the fold. */
  positions: number;
  /** Distinct bots, and how many leaves there are in all. */
  bots: number;
  count: number;
  /** Leaves that have finished, and how many of those made money. */
  closed: number;
  wins: number;
  /** `wins / closed`, or undefined when nothing in scope has closed yet. */
  winRate?: number;
  /**
   * Measured elapsed hours, or 0 when no leaf says when it started.
   *
   * From the earliest start to the latest end when everything in scope has
   * finished, and to `now` while anything is still running. A terminated fold
   * measured to now would grow a runtime for trading that stopped last week,
   * and every per-hour pace derived from it would shrink accordingly.
   */
  hours: number;
  /** How the positions ended, biggest bucket first, and how many ended at all. */
  closeTypes: [string, number][];
  closeTotal: number;
  /** Carried through only for a fold of exactly one leaf; never averaged. */
  returnPct?: number;
}

/** Converts a quote-denominated value into display currency. */
export type ConvertQuote = (value: number, pair: string) => number;

/**
 * `ControllerBrowser`'s `totals`, `scopeFacts` and `closeTypeCounts` in one
 * pass, generalised over the leaf type — plus the two figures the executors
 * page had and the browser did not: fees and win rate.
 *
 * The rules it encodes are the ones the browser already relied on and that a
 * generalisation is most likely to lose: a pace is `undefined` rather than
 * invented when no start time is known (which is why `hours` may be 0 and the
 * caller must check it), a return % is per-leaf and is never summed, and a
 * runtime is measured rather than nominal.
 */
export function foldLeaves(leaves: PerfLeaf[], cv: ConvertQuote, now: number): PerfTotals {
  let realized = 0,
    unrealized = 0,
    net = 0,
    volume = 0,
    fees = 0,
    capital = 0,
    positions = 0,
    closed = 0,
    wins = 0,
    closeTotal = 0;
  let earliest: number | undefined;
  let latestEnd: number | undefined;
  let anyRunning = false;
  const bots = new Set<string>();
  const merged: Record<string, number> = {};

  for (const leaf of leaves) {
    const pair = leaf.pair;
    realized += cv(leaf.realized, pair);
    unrealized += cv(leaf.unrealized, pair);
    net += cv(leaf.net, pair);
    volume += cv(leaf.volume, pair);
    fees += cv(leaf.fees, pair);
    if (leaf.capital > 0) capital += cv(leaf.capital, pair);
    positions += leaf.positions.length;
    bots.add(leaf.bot);

    if (leaf.running) {
      anyRunning = true;
    } else {
      closed += 1;
      if (leaf.net > 0) wins += 1;
      if (leaf.endedAt !== null && (latestEnd === undefined || leaf.endedAt > latestEnd)) {
        latestEnd = leaf.endedAt;
      }
    }
    if (leaf.startedAt !== null && (earliest === undefined || leaf.startedAt < earliest)) {
      earliest = leaf.startedAt;
    }

    for (const [type, count] of Object.entries(leaf.closeTypes)) {
      merged[type] = (merged[type] ?? 0) + count;
      closeTotal += count;
    }
  }

  // A fold with nothing running ends when its last leaf ended; one that still
  // has something live runs to now. A closed fold whose leaves all lost their
  // close times has no end to measure to, so it reports no runtime at all
  // rather than borrowing the clock.
  const end = anyRunning ? now : latestEnd;
  const hours =
    earliest === undefined || end === undefined ? 0 : Math.max(0, (end - earliest) / 3_600_000);

  return {
    realized,
    unrealized,
    net,
    volume,
    fees,
    capital,
    positions,
    bots: bots.size,
    count: leaves.length,
    closed,
    wins,
    winRate: closed > 0 ? wins / closed : undefined,
    hours,
    closeTypes: Object.entries(merged).sort((a, b) => b[1] - a[1]),
    closeTotal,
    returnPct: leaves.length === 1 ? leaves[0].returnPct : undefined,
  };
}
