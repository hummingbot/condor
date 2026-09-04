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

import { agentBucket, BEFORE_LEDGER, OUTSIDE } from "@/components/perf/agentFilter";
import type { DeedIndex, Provenance } from "@/lib/agent-attribution";
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
  /**
   * *How* `agent` was arrived at (FEAT-106), and `"none"` when it was not.
   *
   * Additive beside `agent`, never folded into it: a namespace answer is a
   * proof (the tick's permission callback refused everything else) and a deed
   * answer is a report (a record that could be stale). Nothing that reads
   * `agent` needs to change, and the two surfaces that earn this — the row's
   * provenance marker and the split of the old `Unattributed` bucket — read it
   * instead of guessing from the run key.
   */
  how: Provenance;
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

/**
 * The market a record traded, from wherever the record actually says so.
 *
 * The top-level `trading_pair` is what upstream reports for a live controller
 * and what every executor carries, and it is the one to believe. But it is
 * empty on a controller whose class upstream could not resolve and on a
 * terminated one whose row was rebuilt from a run, and a leaf with no pair is
 * both invisible to the pair grouping and folded as though its quote were
 * dollars. The controller's own **config** declares the pair it was deployed
 * with — that is the field the deploy was written against — so it answers when
 * the report does not.
 *
 * Read off `config` rather than from a second endpoint because the config is
 * already on the record: `ControllerInfo.config` is the deployed YAML and
 * `ExecutorInfo.config` the executor's own, both shipped with the row.
 */
export function pairOf(record: { trading_pair?: string; config?: Record<string, unknown> }): string {
  const reported = (record.trading_pair || "").trim();
  if (reported) return reported;
  const declared = record.config?.trading_pair;
  return typeof declared === "string" ? declared.trim() : "";
}

function finiteOr(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** A live controller, as the browser reports it. */
export function leafFromController(
  c: ControllerInfo,
  agent: string = "",
  how: Provenance = "none",
): PerfLeaf {
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
    how,
    controllerId: c.controller_id || c.controller_name,
    // The specific class when it's known, else the coarse bucket
    // (`generic` / `directional_trading` / `market_making`) a terminated
    // controller's config lookup could still recover, else the dash: see
    // `fill_classes_from_config` and `controllerClassOf`.
    executorType: c.controller_name || c.controller_type || UNKNOWN_LABEL,
    connector: c.connector || "",
    pair: pairOf(c),
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
  how: Provenance = "none",
): PerfLeaf {
  const started = c.deployed_at ? Date.parse(c.deployed_at) : NaN;
  const stopped = run?.stopped_at ? Date.parse(run.stopped_at) : NaN;
  return {
    id: controllerKey(c),
    kind: "controller",
    label: c.controller_id || c.controller_name,
    bot: c.bot_name,
    agent,
    how,
    controllerId: c.controller_id || c.controller_name,
    // The specific class when it's known, else the coarse bucket
    // (`generic` / `directional_trading` / `market_making`) a terminated
    // controller's config lookup could still recover, else the dash: see
    // `fill_classes_from_config` and `controllerClassOf`.
    executorType: c.controller_name || c.controller_type || UNKNOWN_LABEL,
    connector: c.connector || "",
    pair: pairOf(c),
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
  how: Provenance = "none",
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
    how,
    controllerId: attached ? e.controller_id || "" : "",
    executorType: e.type || UNKNOWN_LABEL,
    connector: e.connector || "",
    pair: pairOf(e),
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

export type NodeKind =
  | "fleet"
  | "agent"
  | "bot"
  | "pair"
  | "ctrlType"
  | "orphans"
  | "controller"
  | "executor"
  | "group";

// ── The axes a fleet can be read along (FEAT-107) ──
//
// The tree used to hardcode one nesting — fleet → agent → bot → controller →
// executor — and offer the caller two booleans for whether the middle two were
// drawn. Ownership being the spine is a *reading order*, not a law of the
// records: *"how is SOL-USDC doing across the whole fleet"* is a real question
// that the ownership order actively obstructs. So the order is a list the
// caller hands over, and everything that used to infer structure from an id
// prefix is handed that same list instead.
//
// Controller → executor is not on it, and is not an axis: that is the shape of
// the records themselves, not a way of reading them.

/**
 * One level the caller can ask the tree to spend a row on.
 *
 * The vocabulary lives here, beside {@link keyFor} and the id grammar it
 * writes, rather than in `lib/perf-grouping` with the URL and the presets: the
 * tree cannot be built without it, and a module that the tree imports must not
 * import the tree back.
 */
export type GroupAxis = "agent" | "bot" | "pair" | "ctrlType";

/** Owner first, then the bot that ran it — what `/bots` opens on. */
export const DEFAULT_GROUPING: readonly GroupAxis[] = ["agent", "bot"];

/** The id prefix each axis writes, and the only place the two are joined. */
export const AXIS_PREFIX: Record<GroupAxis, string> = {
  agent: "agent:",
  bot: "bot:",
  pair: "pair:",
  ctrlType: "class:",
};

/** The node kind each axis draws. Named after the axis, so neither can drift. */
export const AXIS_KIND: Record<GroupAxis, NodeKind> = {
  agent: "agent",
  bot: "bot",
  pair: "pair",
  ctrlType: "ctrlType",
};

/**
 * Whether an axis's key is unique across the whole tree on its own.
 *
 * A bot belongs to exactly one owner and an agent is itself, so `bot:alpha`
 * names one row wherever it is nested and every link ever written to one keeps
 * working. A **pair is not** — SOL-USDC is traded by everyone — and neither is
 * a controller class, so a nested `pair:` row has to carry its parent in its id
 * or two owners' SOL-USDC rows become one node, filed under whichever owner's
 * record happened to be read first. That is not a cosmetic collision: it is a
 * row of one agent's money drawn inside another agent's subtree, which is the
 * escape {@link clampScope} exists to prevent, arriving through the id grammar.
 */
const AXIS_UNIQUE: Record<GroupAxis, boolean> = {
  agent: true,
  bot: true,
  pair: false,
  ctrlType: false,
};

/**
 * The separator between a nested row's parent and its own name.
 *
 * Chosen because no run key, bot name, trading pair or controller class can
 * contain it, and because a reader who sees one in a URL can tell what it means.
 */
const NEST = ">";

/** The last segment of a node id: the part that names the node itself. */
function bareId(id: string): string {
  const cut = id.lastIndexOf(NEST);
  return cut < 0 ? id : id.slice(cut + 1);
}

/** The axis an id names, or `null` for an id that names no grouping level. */
export function axisOfNodeId(id: string): GroupAxis | null {
  const bare = bareId(id);
  for (const axis of Object.keys(AXIS_PREFIX) as GroupAxis[]) {
    if (bare.startsWith(AXIS_PREFIX[axis])) return axis;
  }
  return null;
}

/**
 * The key a leaf is filed under on one axis, or `""` when it skips that level.
 *
 * The one place a leaf is bucketed, so adding an axis is one case here and one
 * entry in the three records above. `""` means *skip*, which is what keeps
 * today's behaviour for the leaves that carry no bot: a position opened by hand
 * from `/trade` belongs to no deployment, so a bot level has nothing to say
 * about it and simply does not draw it a row.
 *
 * **Ownership is the one axis with a join behind it.** A leaf carries a run key
 * or nothing, and `agentBucket` (FEAT-106) is what turns *nothing* into one of
 * two answers a reader can act on — *Outside Condor* and *Before the ledger* —
 * rather than an absence. So the agent axis never returns `""`: every record
 * has an owner row, which is the whole point of reading the fleet by owner.
 */
export function keyFor(leaf: PerfLeaf, axis: GroupAxis, deeds?: DeedIndex | null): string {
  switch (axis) {
    case "agent":
      return agentBucket(leaf, deeds);
    // The same condition `botNodeId` reads, said in the leaf's own terms: a
    // leaf that belongs to no controller hangs under no bot either.
    case "bot":
      return leaf.controllerId ? leaf.bot : "";
    case "pair":
      return leaf.pair;
    // A controller is its own class; an executor inherits its controller's by
    // nesting under it, and carries none of its own to be filed by — the
    // vocabularies are different and `controllerClassOf` exists to keep them
    // apart. A controller whose class upstream never reported carries the dash,
    // which is the absence of a name rather than one, so it skips the level.
    case "ctrlType":
      return leaf.kind === "controller" && leaf.executorType !== UNKNOWN_LABEL
        ? leaf.executorType
        : "";
  }
}

/**
 * The bare node id a leaf's key on one axis writes, or `null` for a skipped
 * level. Nested rows are qualified by their parent — see {@link AXIS_UNIQUE}.
 */
export function axisNodeId(
  leaf: PerfLeaf,
  axis: GroupAxis,
  deeds?: DeedIndex | null,
  parentId = "",
): string | null {
  const key = keyFor(leaf, axis, deeds);
  if (!key) return null;
  const bare = `${AXIS_PREFIX[axis]}${key}`;
  return AXIS_UNIQUE[axis] || !parentId ? bare : `${parentId}${NEST}${bare}`;
}

/**
 * Every grouping row a leaf hangs inside, outermost first.
 *
 * The ancestry an id used to spell out on its own, computed from the leaf and
 * the order instead — which is what a reorderable nesting costs and what
 * {@link fallbackChain} spends it on.
 */
export function axisChain(
  leaf: PerfLeaf,
  grouping: readonly GroupAxis[],
  deeds?: DeedIndex | null,
): string[] {
  const ids: string[] = [];
  let parentId = "";
  for (const axis of grouping) {
    const id = axisNodeId(leaf, axis, deeds, parentId);
    if (!id) continue;
    ids.push(id);
    parentId = id;
  }
  return ids;
}

/**
 * The kinds that name a *run* rather than a part of one: an agent, a bot, and
 * the one bucket that holds every executor no controller claims. These are the
 * fleet's own children — the rows a reader compares against each other — as
 * against a `controller` or an `executor`, which are always a detail *of* one
 * of them.
 *
 * `group` is deliberately not here any more: a `group` node is now always a
 * child of `orphans` rather than of the fleet (see `buildTree`'s `groupFor`),
 * so `orphans` is the one that needs the fleet-child treatment this set gives.
 *
 * Every grouping axis is here (FEAT-107), for the reason `agent` and `bot`
 * already were: whichever order the reader picked, the outermost level is the
 * list of rows they are comparing, and a `pair:` row under `?groupBy=pair` is
 * doing exactly the job a `bot:` row does under the default.
 *
 * It lives here, two lines under {@link NodeKind}, so that adding a kind and
 * deciding whether it is one of these is a single edit in a single place. It is
 * deliberately *not* derived from `nodeDepth`: that function reads a level's
 * place in the grouping the caller asked for, which is a different question
 * from the one asked here.
 */
export const TOP_LEVEL_KINDS: ReadonlySet<NodeKind> = new Set<NodeKind>([
  "agent",
  "bot",
  "pair",
  "ctrlType",
  "orphans",
]);

/**
 * Which of the fleet's own children `openRows` (in `PerfBrowser`) draws open
 * on arrival, before the reader has clicked a chevron.
 *
 * Deliberately not `TOP_LEVEL_KINDS`, and not derived from it: that set asks
 * "which kinds are the fleet's own children"; this one asks "which of those
 * default open", and the two answers differ by exactly `group`. ARCH-318
 * created the `grp:` row *because* the bucket of unclaimed executors, drawn
 * open at depth 0, buried the bot rows the reader came for — auto-opening it
 * here would reinstate that. `agent` belongs, though, for the same reason
 * `bot` always has: a bot nested under an agent row (`buildTree` parents it
 * `agentFor(leaf) ?? fleet`) must still be reachable without a click, or the
 * agent-operated fleets — the ones with the most structure — are exactly the
 * ones where "bots are visible on arrival" silently stops being true.
 *
 * `pair` and `ctrlType` are deliberately **not** here, and that is the split
 * this set exists to keep: they are fleet children like the two above (see
 * {@link TOP_LEVEL_KINDS}) and they still do not default open. A bot row is
 * opened to reach the controllers *under* it, which is what the reader came
 * for; a pair row **is** what the reader came for — *"how is SOL-USDC doing
 * across the fleet"* is answered by the row itself, and opening all of them on
 * arrival would draw the flat list the grouping was chosen to replace.
 */
export const AUTO_OPEN_KINDS: ReadonlySet<NodeKind> = new Set<NodeKind>(["agent", "bot"]);

/**
 * The ids `openRows` folds into what is drawn without being asked: every row
 * of an `AUTO_OPEN_KINDS` kind the reader has not shut.
 *
 * A plain function of the index and `shut` rather than a walk of its own, so
 * the one loop `PerfBrowser` used to hardcode to `kind === "bot"` reads —
 * and is tested — as what it now means: open by default unless closed.
 */
export function autoOpenIds(
  nodes: ReadonlyMap<string, PerfNode>,
  shut: ReadonlySet<string>,
): Set<string> {
  const ids = new Set<string>();
  for (const node of nodes.values()) {
    if (AUTO_OPEN_KINDS.has(node.kind) && !shut.has(node.id)) ids.add(node.id);
  }
  return ids;
}

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
 * server there are dozens of them, each under a different dead controller id.
 * They are not anonymous, though — each is filed under the controller id it
 * carries as its bot (`main`, for one opened from `/trade`), which is the key
 * this returns. `buildTree` nests every such bucket under one "Unattached" row
 * rather than drawing each at depth 0, so the count of them stays legible
 * instead of burying the bot rows the reader came for.
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
 * a `"bot"` axis puts it back as a level, because a bot is the thing you *act*
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
 * **The order of those levels is the caller's too** (FEAT-107): `grouping` is
 * a list of {@link GroupAxis}, applied outermost first, and *"which levels"*
 * and *"in what order"* are now one question with one answer. `["agent",
 * "bot"]` is the fleet read by owner; `["pair"]` is the same leaves read by
 * what they trade. Every level follows the same three rules — a leaf whose
 * {@link keyFor} is `""` skips it, a level with one key in it is the caller's
 * to leave out, and no level ever changes what anything folds to.
 *
 * An executor whose controller is gone *and* whose agent is unknown belongs to
 * no deployment and to nobody, but it is not nameless: it hangs under a
 * **group** row named for the controller id it carries (see
 * {@link groupNodeId}). Most dead controller ids are distinct from one another
 * — one executor each — so bucketing by id alone still leaves dozens of bare
 * rows at the same indentation as a bot. They are gathered a second time,
 * under one **orphans** row (`"Unattached"`), so the terminated side of a real
 * server draws one collapsible row instead of dozens ahead of the bots the
 * reader came for. Both rows exist only when the tree has levels at all, so
 * the flat tree still hangs those executors off the fleet directly.
 */
export function buildTree(
  leaves: PerfLeaf[],
  rootLabel = "All",
  {
    grouping = [],
    deeds = null,
  }: { grouping?: readonly GroupAxis[]; deeds?: DeedIndex | null } = {},
): PerfNode {
  const fleet = makeNode("all", "fleet", rootLabel);
  const levels = new Map<string, PerfNode>();
  const groups = new Map<string, PerfNode>();
  const controllers = new Map<string, PerfNode>();
  const orphansOf = new Map<string, PerfNode>();

  /**
   * The controller record that decides where a controller's row hangs.
   *
   * Its executors may be read first, and they know less than it does: an
   * executor carries no class of its own to be grouped by, and under
   * `?groupBy=ctrlType` the row's parent would then depend on which record
   * happened to come off the wire first. The controller's own leaf answers for
   * the row whenever the population has one.
   */
  const anchors = new Map<string, PerfLeaf>();
  for (const leaf of leaves) {
    if (leaf.kind !== "controller") continue;
    const id = controllerNodeId(leaf);
    if (id && !anchors.has(id)) anchors.set(id, leaf);
  }

  // Insertion order throughout: the first record filed under a key is what puts
  // that row in the list, so rows come out in the order the caller sorted its
  // records — the page sorts its controllers before handing them over.
  const levelFor = (leaf: PerfLeaf): PerfNode => {
    let parent = fleet;
    for (const axis of grouping) {
      const id = axisNodeId(leaf, axis, deeds, parent === fleet ? "" : parent.id);
      if (!id) continue;
      let node = levels.get(id);
      if (!node) {
        // The key itself, which is both the id's tail and what the row says out
        // loud — the sidebar formats it (a run key is split for display, a bot
        // name shortened), so a row can be labelled with nothing else in hand.
        node = makeNode(id, AXIS_KIND[axis], keyFor(leaf, axis, deeds));
        levels.set(id, node);
        parent.children.push(node);
      }
      parent = node;
    }
    return parent;
  };

  // The one row every executor nobody claims hangs under, created the first
  // time one is seen and pushed onto its level exactly once — so a terminated
  // side with 42 dead controller ids draws one collapsible "Unattached" row
  // ahead of the bots, not 42.
  //
  // Absent from a tree with no levels at all, for the same reason a bot level
  // is: a chevron that tells the reader nothing, and the flat tree keeps the
  // shape its tests already pin.
  const orphansNode = (under: PerfNode): PerfNode | null => {
    if (grouping.length === 0) return null;
    let node = orphansOf.get(under.id);
    if (!node) {
      node = makeNode(under === fleet ? "orphans" : `${under.id}${NEST}orphans`, "orphans", "Unattached");
      orphansOf.set(under.id, node);
      under.children.push(node);
    }
    return node;
  };

  // The bucket for one dead controller id's executors: memoised, created the
  // first time one is seen, and pushed onto `orphansNode()` rather than onto
  // the level directly — the per-id split is still worth keeping once the
  // reader opens "Unattached", it just should not cost a row of its own before
  // they do.
  const groupFor = (leaf: PerfLeaf, under: PerfNode): PerfNode | null => {
    const bucket = orphansNode(under);
    if (!bucket) return null;
    const bare = groupNodeId(leaf);
    if (!bare) return null;
    const id = under === fleet ? bare : `${under.id}${NEST}${bare}`;
    let node = groups.get(id);
    if (!node) {
      // The controller id the executor carries, said out loud — `main`, for a
      // position opened by hand. It is the one name the record has.
      node = makeNode(id, "group", leaf.bot);
      groups.set(id, node);
      bucket.children.push(node);
    }
    return node;
  };

  const controllerFor = (leaf: PerfLeaf): PerfNode | null => {
    const id = controllerNodeId(leaf);
    if (!id) return null;
    let node = controllers.get(id);
    if (!node) {
      node = makeNode(id, "controller", leaf.controllerId);
      controllers.set(id, node);
      levelFor(anchors.get(id) ?? leaf).children.push(node);
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
    // off the agent whose session tagged it. An owner is a better answer than a
    // filing key, so an executor whose owner the tree actually drew a row for
    // is never left over for the bucket to hold — while one that nobody owns
    // is gathered under "Unattached" inside whatever level it landed in.
    let parent = controllerFor(leaf);
    if (!parent) {
      const under = levelFor(leaf);
      const owned = !!leaf.agent && grouping.includes("agent");
      parent = (owned ? null : groupFor(leaf, under)) ?? under;
    }
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

  sinkUnowned(fleet);
  return fleet;
}

/**
 * Push the two unowned buckets to the end of every list of owner rows.
 *
 * The reason `agentFilter` already gives for the bubbles: they are *"the bucket
 * for everything the fleet map could not credit, not one more owner"*, and on a
 * real server they are usually the biggest. Naming them after the named runs
 * keeps the runs readable as a list. Insertion order decides everything else in
 * this tree, and deliberately — this is the one exception, because the order
 * records arrive in has nothing to say about which of them is an answer.
 *
 * Between themselves they take the order the bubbles give them, and for the
 * same reason: *Outside Condor* is a standing fact, *Before the ledger* is the
 * one that drains to zero as the log fills.
 */
const UNOWNED_ORDER = [OUTSIDE, BEFORE_LEDGER];

function sinkUnowned(node: PerfNode): void {
  const rank = (child: PerfNode) =>
    child.kind === "agent" ? UNOWNED_ORDER.indexOf(bareId(child.id).slice(6)) : -1;
  if (node.children.some((child) => rank(child) >= 0)) {
    node.children = [
      ...node.children.filter((child) => rank(child) < 0),
      ...node.children.filter((child) => rank(child) >= 0).sort((a, b) => rank(a) - rank(b)),
    ];
  }
  node.children.forEach(sinkUnowned);
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
 * was the same number only while the tree was flat: with a bot level on, every
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
export function fallbackChain(
  scopeId: string,
  leaf?: PerfLeaf,
  { grouping = DEFAULT_GROUPING, deeds = null }: ScopeOpts = {},
): string[] {
  const chain = [scopeId];
  if (leaf) {
    const ctrl = controllerNodeId(leaf);
    if (ctrl) chain.push(ctrl);
  }
  // The grouping rows the leaf sits inside, innermost first — the ancestry the
  // id used to spell out on its own. A level the caller left out is simply not
  // in the tree, so `resolveScope` walks past its candidate to the next one:
  // a controller that a filter removed leaves the reader on the bot that ran
  // it, or on the owner above that, rather than on the whole fleet.
  const levels = leaf ? axisChain(leaf, grouping, deeds).reverse() : [];
  // The bucket the leaf hangs in when no controller claims it, which sits just
  // inside those rows: without it an executor row a filter removed falls all
  // the way back to the fleet rather than to the group it was sitting in. Only
  // a leaf can supply it, since a group is not an ancestry an executor id
  // spells out; a group scope answers for itself as the head of the chain.
  const bare = leaf ? groupNodeId(leaf) : null;
  if (bare) {
    const under = levels[0] ?? "";
    chain.push(under ? `${under}${NEST}${bare}` : bare);
  }
  // Read off the id as well as off the leaf, so a `ctrl:` scope with no leaf to
  // hand still knows the bot it named. An `agent:` scope answers for itself.
  const bot = botOfNodeId(scopeId);
  if (bot) chain.push(`bot:${bot}`);
  chain.push(...levels, "all");

  const depth = nodeDepth(scopeId, grouping);
  const seen = new Set<string>();
  return chain.filter(
    (id) => nodeDepth(id, grouping) <= depth && !seen.has(id) && (seen.add(id), true),
  );
}

/**
 * How far down the tree an id sits, read off the id and the order that built it.
 *
 * This is what a reorderable nesting actually costs. The depths used to be
 * constants — *"an `agent:` sits at 1"* — because there was one nesting and an
 * id prefix therefore named a level; with the order in the caller's hands an
 * `agent:` row sits wherever the caller put it, and inferring otherwise would
 * be a lie in the one function whose answers must never be.
 *
 * An id that names no level of *this* grouping — a `bot:` from a link written
 * under `?groupBy=pair`, a retired `type:` group from before the class grouping
 * went away, or plain nonsense — is treated as the *shallowest* thing there is,
 * so the only candidate that can serve it is the fleet. That is the right
 * answer rather than a giving-up: the link named a level this reading of the
 * fleet does not have, and the report that contains it is the whole fleet.
 *
 * These are *relative* depths, and only the ordering is load-bearing: the rule
 * they serve is "a candidate is never deeper than the scope it replaces". The
 * `ctrl:` and `exec:` id *grammars* are untouched, so links written before any
 * of this still resolve exactly as they did.
 */
function nodeDepth(id: string, grouping: readonly GroupAxis[]): number {
  const axis = axisOfNodeId(id);
  if (axis) {
    const at = grouping.indexOf(axis);
    return at < 0 ? 0 : at + 1;
  }
  // The unattached rows sit just inside the last grouping level and just
  // outside a controller, which is where a lost executor has to be able to land.
  const bare = bareId(id);
  if (bare === "orphans" || bare.startsWith("grp:")) return grouping.length + 1;
  if (bare.startsWith("ctrl:")) return grouping.length + 1;
  if (bare.startsWith("exec:")) return grouping.length + 2;
  return 0;
}

/** How a scope is read: the order the tree was built in, and the ledger behind it. */
export interface ScopeOpts {
  grouping?: readonly GroupAxis[];
  deeds?: DeedIndex | null;
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
  opts: ScopeOpts = {},
): string {
  return fallbackChain(scopeId, leaf, opts).find((id) => nodes.has(id)) ?? "all";
}

/**
 * The scope a browser with a **floor** actually lands on (FEAT-108).
 *
 * A host can root the browser at something narrower than the fleet — the agent
 * workspace roots it at the one agent whose screen it is — and that root is a
 * *floor*, not a default. A default would be escapable: one click on a sibling
 * row, one stale link, one filter that removed the root's last leaf, and the
 * workspace would be reporting another agent's money under this agent's name.
 *
 * So the rule is a clamp, and it lives here beside `resolveScope` because it is
 * the same job — deciding which node a requested scope really means — asked
 * from the other end: `resolveScope` walks *up* until it finds something that
 * exists, and this walks up until it finds the root, landing on the root when
 * it never does.
 *
 * Containment is read off the **tree**, not off the id grammar, deliberately.
 * `fallbackChain` infers ancestry from an id's prefix, which encodes one
 * nesting order; a tree that is grouped some other way would make that
 * inference a lie, and a lie here is an escape. `ancestorChain` asks the tree
 * that was actually built, so the clamp holds under any grouping.
 *
 * `rootScope` of `"all"` — the whole fleet, which is what `/bots` roots at —
 * clamps nothing, and costs no walk.
 */
export function clampScope(root: PerfNode, scopeId: string, rootScope: string): string {
  if (!rootScope || rootScope === "all" || scopeId === rootScope) return scopeId;
  return ancestorChain(root, scopeId).includes(rootScope) ? scopeId : rootScope;
}

/**
 * A stand-in for a root whose node is not in the tree.
 *
 * A rooted browser whose root has nothing in it — an agent that has deployed
 * nothing yet, or whose last leaf a filter just removed — must report *nothing*,
 * and the one thing it must never do is fall back to the fleet: that is the
 * escape {@link clampScope} exists to prevent, arriving through the back door.
 * An empty node of the right kind reports an empty scope, which is the truth.
 */
export function emptyScopeNode(id: string, label: string): PerfNode {
  return { id, kind: kindOfNodeId(id), label, leaves: [], children: [] };
}

/**
 * The kind of node an id names, read off the id alone.
 *
 * The inverse of the `id` grammar `buildTree` writes. Only used where a node
 * has to be conjured without a tree to look it up in ({@link emptyScopeNode}).
 */
function kindOfNodeId(id: string): NodeKind {
  const axis = axisOfNodeId(id);
  if (axis) return AXIS_KIND[axis];
  const bare = bareId(id);
  if (bare.startsWith("grp:")) return "group";
  if (bare.startsWith("ctrl:")) return "controller";
  if (bare.startsWith("exec:")) return "executor";
  if (bare === "orphans") return "orphans";
  return "fleet";
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
