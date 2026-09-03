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
  /**
   * The run key of the `(agent, strategy)` that owns it, or `""` for none.
   *
   * The sibling of `bot`, and filled in the same way and for the same reason:
   * **the caller fills it in, because the record does not carry one.** Neither
   * a `ControllerInfo` nor an `ExecutorInfo` names an agent — ownership is a
   * fact about the bot's *name* (its namespace) or about the session id an
   * executor was tagged with, so it is a join the page does and hands over
   * (`lib/agent-attribution`, FEAT-096).
   *
   * `""` is not an error: most fleets have no agent in them at all, and a bot
   * outside every namespace is honestly nobody's.
   */
  agent: string;
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
export function leafFromController(c: ControllerInfo, agent: string = ""): PerfLeaf {
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
    agent,
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
export function leafFromTerminatedController(
  c: ControllerInfo,
  run?: BotRunInfo,
  agent: string = "",
): PerfLeaf {
  const started = c.deployed_at ? Date.parse(c.deployed_at) : NaN;
  const stopped = run?.stopped_at ? Date.parse(run.stopped_at) : NaN;
  return {
    id: controllerKey(c),
    kind: "controller",
    label: c.controller_id || c.controller_name,
    bot: c.bot_name,
    agent,
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
export function leafFromExecutor(
  e: ExecutorInfo,
  bot: string = UNATTACHED_BOT,
  agent: string = "",
): PerfLeaf {
  const running = isExecutorActive(e.status);
  const closedAt = e.close_timestamp > 0 ? toMs(e.close_timestamp) : null;
  const attached = !!bot && bot !== UNATTACHED_BOT;
  return {
    id: e.id,
    kind: "executor",
    label: e.id,
    bot: attached ? bot : e.controller_id || UNATTACHED_BOT,
    agent,
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

/**
 * Which granularity of record a reader is reporting on (ARCH-317).
 *
 * The two populations differ on this more than on anything else: a terminated
 * fold mixes finished controllers with the executors that belong to no run, and
 * a running one hangs live executors under every controller. `both` is the
 * tree as it has always been.
 */
export type Grain = "both" | "controllers" | "executors";

/**
 * Whether a leaf belongs in a tree drawn at this granularity.
 *
 * One predicate over `leaf.kind`, and the whole of the feature: `buildTree`'s
 * spine rule — a node folds its own leaf when it has one and its children's
 * when it does not — does the rest. Dropping every executor leaves each
 * controller row reporting its own record, unchanged, with no children under
 * it; dropping every controller leaves each controller row folding the
 * executors that survived, which is exactly the "step aside" the executor-type
 * filter has always relied on.
 *
 * It lives here rather than beside the panel for the reason `controllerClassOf`
 * does (the ARCH-300 split): it is a rule about a leaf, and a test can reach it
 * without mounting a browser.
 */
export function matchesGrain(leaf: PerfLeaf, grain: Grain): boolean {
  if (grain === "controllers") return leaf.kind !== "executor";
  if (grain === "executors") return leaf.kind !== "controller";
  return true;
}

// ── The tree ──

export type NodeKind = "fleet" | "agent" | "bot" | "controller" | "executor" | "group";

/**
 * The kinds that name a *run* rather than a part of one: an agent, a bot, and
 * the bucket of executors no controller claims. These are the fleet's own
 * children — the rows a reader compares against each other — as against a
 * `controller` or an `executor`, which are always a detail *of* one of them.
 *
 * It lives here, two lines under {@link NodeKind}, so that adding a kind and
 * deciding whether it is one of these is a single edit in a single place. It is
 * deliberately *not* derived from `nodeDepth`: that function reads an id's
 * ancestry (a `bot:` sits at 2, under the `agent:` that may operate it), which
 * is a different question from the one asked here.
 */
export const TOP_LEVEL_KINDS: ReadonlySet<NodeKind> = new Set<NodeKind>([
  "agent",
  "bot",
  "group",
]);

export interface PerfNode {
  /** `all` | `agent:runKey` | `bot:name` | `grp:name` | `ctrl:k` | `exec:id` */
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

/**
 * The node id of the bot a leaf hangs under, or `null` for a leaf that hangs
 * under none.
 *
 * "None" is exactly the leaf that belongs to no controller either — a position
 * opened by hand from `/trade`, filed under its own controller id as its bot
 * (see {@link leafFromExecutor}). There is no deployment behind it to stop, so
 * it is not a bot's, and this keeps returning `null` for it: the contract is
 * "the bot a leaf hangs under", and `fallbackChain` reads it as one. What such
 * a leaf hangs under instead is a bucket — see {@link groupNodeId}.
 */
export function botNodeId(leaf: PerfLeaf): string | null {
  return controllerNodeId(leaf) === null ? null : `bot:${leaf.bot}`;
}

/**
 * The node id of the bucket a leaf that belongs to no controller hangs under,
 * or `null` for every leaf that does belong to one.
 *
 * These are the hand-opened positions, and on the terminated side of a real
 * server there are dozens of them: 42 bare executor ids drawn at depth 0, ahead
 * of the 85 bot rows the reader came for, with no chevron to fold them away
 * because a row with no children has none. They are not anonymous, though —
 * each is filed under the controller id it carries as its bot (`main`, for one
 * opened from `/trade`), which is the key this returns.
 *
 * A `grp:` prefix of its own rather than `bot:` is what keeps the bucket from
 * *becoming* a bot: `botOfNodeId` matches `bot:` and `ctrl:` only, so the
 * sidebar's Stop button and the header's run actions cannot reach a deployment
 * that does not exist, by construction rather than by a check someone has to
 * remember. `countNodes(tree, "bot")` stays honest for the same reason — the
 * `Select all` line would otherwise report 86 bots for a fleet running 85.
 */
export function groupNodeId(leaf: PerfLeaf): string | null {
  return controllerNodeId(leaf) === null ? `grp:${leaf.bot}` : null;
}

/**
 * The bot a `bot:` or `ctrl:` node id names, read off the id alone.
 *
 * The sidebar's Stop button needs the bot's *full* name — the row shows a
 * shortened one — and the row has only its node id by then. `null` for any
 * other id, which is what keeps the button off rows that are not a bot's.
 */
export function botOfNodeId(id: string): string | null {
  if (id.startsWith("bot:")) return id.slice(4) || null;
  if (id.startsWith("ctrl:")) return id.split(":")[1] || null;
  return null;
}

/**
 * The node id of the agent a leaf hangs under, or `null` for a leaf nobody owns.
 *
 * The run key is the whole id: it is already unique across the fleet, it is
 * what the URL carries (`?scope=agent:brigado.brl_mm`), and it is what
 * `StrategyDetail`'s *View in fleet* link is built from.
 */
export function agentNodeId(leaf: PerfLeaf): string | null {
  return leaf.agent ? `agent:${leaf.agent}` : null;
}

/** The run key an `agent:` node id names, or `null` for any other id. */
export function agentOfNodeId(id: string): string | null {
  return id.startsWith("agent:") ? id.slice(6) || null : null;
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
 * The class-of-thing grouping is gone for good — it is a bubble above the tree,
 * combinable and one click shallower than a chevron ever was. **Bot is not**:
 * `groupByBot` puts it back as a level, because a bot is the thing you *act*
 * on. Stopping is a per-bot verb with no per-controller equivalent, and a flat
 * list gives it nowhere to live: the reader had to narrow the bubbles down to
 * one bot before the fleet row became that bot and grew a Stop button, which is
 * a filter interaction standing in for a selection. A bot row is that selection
 * said directly, and it carries the button.
 *
 * It is a level the caller asks for rather than one that is always there: a
 * fleet running a single bot would spend a chevron saying so. `PerfBrowser`
 * turns it on exactly when more than one bot is in scope.
 *
 * **Agent is the same kind of level, one higher** (FEAT-096): one row per
 * `(agent, strategy)`, folding everything that strategy operates. It is what
 * makes "an agent is driving these four controllers and that loose executor" a
 * thing the fleet page can say, and it is expressed structurally rather than
 * as a caption — a bot the agent deployed nests under it, and a standalone
 * executor it created, which has no controller and therefore no bot, hangs off
 * the agent directly instead of off the fleet.
 *
 * An executor whose controller is gone *and* whose agent is unknown belongs to
 * no deployment and to nobody, but it is not nameless: it hangs under a
 * **group** row named for the controller id it carries (see
 * {@link groupNodeId}), so the dozens of hand-opened positions on a real
 * server's terminated side are one collapsible row rather than dozens of bare
 * ids drawn at the same indentation as a bot. It is a level rather than a bot
 * because there is no deployment behind it to stop, and a kind of its own
 * because the counts that say "N bots" must keep meaning it. The group level
 * follows `groupByBot`, so the flat tree still hangs those executors off the
 * fleet directly.
 */
export function buildTree(
  leaves: PerfLeaf[],
  rootLabel = "All",
  { groupByBot = false, groupByAgent = false }: { groupByBot?: boolean; groupByAgent?: boolean } = {},
): PerfNode {
  const fleet = makeNode("all", "fleet", rootLabel);
  const agents = new Map<string, PerfNode>();
  const bots = new Map<string, PerfNode>();
  const groups = new Map<string, PerfNode>();
  const controllers = new Map<string, PerfNode>();

  // Insertion order once more: the first record an agent owns is what puts it
  // in the list, so agents come out in the order the caller sorted its records.
  const agentFor = (leaf: PerfLeaf): PerfNode | null => {
    if (!groupByAgent) return null;
    const id = agentNodeId(leaf);
    if (!id) return null;
    let node = agents.get(id);
    if (!node) {
      // The run key, which is both the id and what the row says out loud
      // (`lib/agent-attribution`'s `runKeyLabel` splits it for display) — so a
      // row can be labelled without the fleet map in hand.
      node = makeNode(id, "agent", leaf.agent);
      agents.set(id, node);
      fleet.children.push(node);
    }
    return node;
  };

  // Insertion order again: the first controller of a bot is what puts the bot
  // in the list, so bots come out in the order the caller sorted controllers.
  const botFor = (leaf: PerfLeaf): PerfNode | null => {
    if (!groupByBot) return null;
    const id = botNodeId(leaf);
    if (!id) return null;
    let node = bots.get(id);
    if (!node) {
      // The raw name: shortening it is the sidebar's job, and the
      // Stop button needs the name the API knows.
      node = makeNode(id, "bot", leaf.bot);
      bots.set(id, node);
      (agentFor(leaf) ?? fleet).children.push(node);
    }
    return node;
  };

  // The bucket for the executors nobody claims, built exactly as `botFor` is:
  // memoised, created the first time one is seen, and pushed onto the fleet in
  // that order — so it lands wherever its first executor was, which on the
  // terminated side is still ahead of the bot rows. That is one collapsible row
  // rather than 42, which was the whole complaint; sorting the fleet's children
  // is a separate question.
  //
  // Off when `groupByBot` is off, for the same reason the bot level is: a fleet
  // running a single bot would spend a chevron saying so, and the flat tree
  // keeps the shape its tests already pin.
  const groupFor = (leaf: PerfLeaf): PerfNode | null => {
    if (!groupByBot) return null;
    const id = groupNodeId(leaf);
    if (!id) return null;
    let node = groups.get(id);
    if (!node) {
      // The controller id the executor carries, said out loud — `main`, for a
      // position opened by hand. It is the one name the record has.
      node = makeNode(id, "group", leaf.bot);
      groups.set(id, node);
      fleet.children.push(node);
    }
    return node;
  };

  // Insertion order is the caller's order, which is the order the sidebar
  // draws — the page sorts its controllers before handing them over.
  const controllerFor = (leaf: PerfLeaf): PerfNode | null => {
    const id = controllerNodeId(leaf);
    if (!id) return null;
    let node = controllers.get(id);
    if (!node) {
      node = makeNode(id, "controller", leaf.controllerId);
      controllers.set(id, node);
      (botFor(leaf) ?? agentFor(leaf) ?? fleet).children.push(node);
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
    // A standalone executor an agent created has no controller and no bot, and
    // this is where it stops being a mystery row on the fleet root: it hangs
    // off the agent whose session tagged it. The agent is asked before the
    // bucket because it is the better answer — an owner, not a filing key — and
    // an executor it owns is never left over for the bucket to hold.
    const parent = controllerFor(leaf) ?? agentFor(leaf) ?? groupFor(leaf) ?? fleet;
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

/**
 * How many nodes of one kind the tree holds, at whatever depth they sit.
 *
 * The sidebar's "N controllers" used to count the fleet's own children, which
 * was the same number only while the tree was flat: with `groupByBot` on, every
 * controller is a grandchild and that count read zero.
 */
export function countNodes(root: PerfNode, kind: NodeKind): number {
  let n = 0;
  const walk = (node: PerfNode) => {
    if (node.kind === kind) n += 1;
    node.children.forEach(walk);
  };
  walk(root);
  return n;
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
 * stale `type:` id from a link written before the class grouping was retired
 * reads as depth-0 here and so falls all the way back to the fleet, which is
 * the report it named.
 */
export function fallbackChain(scopeId: string, leaf?: PerfLeaf): string[] {
  const chain = [scopeId];
  if (leaf) {
    const ctrl = controllerNodeId(leaf);
    if (ctrl) chain.push(ctrl);
  }
  // The bot level is optional (see `buildTree`), so this candidate is often not
  // in the tree at all — `resolveScope` simply walks past it to the fleet. When
  // it *is* there it is the right landing place: a controller that a filter
  // removed leaves the reader on the bot that ran it rather than on the whole
  // fleet. Read off the id, so it serves a `ctrl:` scope with no leaf to hand.
  const bot = botOfNodeId(scopeId) ?? (leaf ? botNodeId(leaf)?.slice(4) : undefined);
  if (bot) chain.push(`bot:${bot}`);
  // The bucket the leaf hangs in when no controller claims it, which is that
  // same candidate one level up: without it an executor row a filter removed
  // falls all the way back to the fleet rather than to the group it was sitting
  // in. Only a leaf can supply it, since a group is not an ancestry an executor
  // id spells out; a group scope answers for itself as the head of the chain.
  const group = leaf ? groupNodeId(leaf) : null;
  if (group) chain.push(group);
  // And the agent above it, same rule: a bot row that a filter removed leaves
  // the reader on the agent that operates it rather than on the whole fleet.
  // Only a leaf can supply it — a `bot:`/`ctrl:` id names a bot, and a bot's
  // name does not say who owns it without the fleet map, which this pure
  // function does not have. An `agent:` scope answers for itself.
  const agent = agentOfNodeId(scopeId) ?? leaf?.agent;
  if (agent) chain.push(`agent:${agent}`);
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
 * An id that names no level of this tree — a retired `type:` group from a link
 * written before the class grouping went away, or plain nonsense — is treated
 * as the *shallowest* thing there is, so the only candidate that can serve it
 * is the fleet. A stale `bot:` id is *not* nonsense any more: it is a level
 * again, and lands on the bot when the tree groups by bot and on the fleet when
 * it does not — either way a report about that bot, never one of its
 * controllers, which would be much narrower than the link asked for.
 *
 * These are *relative* depths, and only the ordering is load-bearing: the rule
 * they serve is "a candidate is never deeper than the scope it replaces". The
 * agent level (FEAT-096) pushed every number below it down by one; the `ctrl:`
 * and `exec:` id *grammars* are untouched, so links written before it still
 * resolve exactly as they did.
 */
function nodeDepth(id: string): number {
  if (id.startsWith("agent:")) return 1;
  // A child of the fleet, like an agent row — and never an ancestor of a
  // controller, since the leaves it holds belong to none.
  if (id.startsWith("grp:")) return 1;
  if (id.startsWith("bot:")) return 2;
  if (id.startsWith("ctrl:")) return 3;
  if (id.startsWith("exec:")) return 4;
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
