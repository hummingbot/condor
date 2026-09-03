import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Database,
  Layers,
  Loader2,
  Pause,
  Play,
  Rocket,
  ScrollText,
  Server,
  SlidersHorizontal,
  Square,
  TerminalSquare,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "react-router-dom";

import { EditorModal } from "@/components/editor/EditorModal";
import { ControllerPnlChart } from "@/components/bots/ControllerPnlChart";
import { DeployBotDialog } from "@/components/bots/DeployBotDialog";
import { LogsSection } from "@/components/bots/LogsSection";
import { PnlEvolutionChart } from "@/components/bots/PnlEvolutionChart";
import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { DetailPanel } from "@/components/perf/ExecutorTable";
import { ExecutorRows, StopConfirmDialog } from "@/components/perf/ExecutorRows";
import { useExecutorStop } from "@/components/perf/executorActions";
import {
  agentBucketLabel,
  agentOptions,
  inRun,
  matchesAgents,
  parseRunParam,
  runChipLabel,
  runOwner,
  runRecords,
} from "@/components/perf/agentFilter";
import { BubbleGroup, type BubbleOption } from "@/components/perf/FilterBubbles";
import { PopulationToggle } from "@/components/perf/PopulationToggle";
import { PositionsTable } from "@/components/perf/PositionsTable";
import { ScopeTree, StatusDot } from "@/components/perf/ScopeTree";
import { YamlConfigEditor } from "@/components/perf/YamlConfigEditor";
import { ArchivedBotDetail } from "@/components/bots/ArchivedBotDetail";
import {
  api,
  type BotLogEntry,
  type BotSummary,
  type ControllerInfo,
  type BotRunInfo,
  type ControllerPerformanceSnapshot,
  type ExecutorInfo,
} from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import {
  formatCurrencyVolume,
  formatCurrencyPnl,
  formatRuntimeHours,
  isExecutorActive,
  pnlColor,
  shortBotName,
  toMs,
} from "@/lib/formatters";
import {
  ancestorChain,
  agentOfNodeId,
  autoOpenIds,
  botOfNodeId,
  buildTree,
  clampScope,
  collectLeaves,
  controllerClassOf,
  countNodes,
  emptyScopeNode,
  foldLeaves,
  indexTree,
  leafFromController,
  leafFromExecutor,
  leafFromTerminatedController,
  matchesGrain,
  parsePopulation,
  resolveScope,
  runStatus,
  UNATTACHED_BOT,
  visibleNodeIds,
  type Grain,
  type PerfNode,
  type Population,
  type PerfLeaf,
} from "@/lib/perf-tree";
import {
  collapseGrouping,
  groupingForRoot,
  parseGrouping,
  rootAxis,
} from "@/lib/perf-grouping";
import { resolvePerfSeries, scopeInterval } from "@/lib/perf-history";
import { chartNotice } from "@/lib/perf-notices";
import { buildPositionRows, parseSide, type PositionRow } from "@/lib/perf-positions";
import { aggregatePnlSeries, snapshotsFromRunHistory } from "@/lib/pnl-chart";
import { buildAttributor, runWindows } from "@/lib/run-attribution";
import { dropDeletedRunQueries } from "@/lib/run-deletion";
import type { ConvertFn } from "@/lib/rates";
import { useViewFacts } from "@/lib/viewFacts";
import {
  attributionOf,
  loopFacts,
  loopStatus,
  ownerOf,
  ownerTitle,
  runKeyLabel,
  type DeedIndex,
  type FleetOwner,
} from "@/lib/agent-attribution";
import { AgentScopeHeader } from "./AgentScopeHeader";

/**
 * What an empty root row is called, read off its id alone (FEAT-108).
 *
 * Only reached when the root has no node in the tree — an agent that has
 * deployed nothing — so there is no label from `buildTree` to borrow and the id
 * is the only name there is.
 */
function rootScopeLabel(id: string): string {
  const agent = agentOfNodeId(id);
  if (agent) return runKeyLabel(agent);
  return botOfNodeId(id) ?? id;
}

/** `3, "bot"` → `"3 bots"`. Thousands separated, because a fleet's closed set runs to five figures. */
function plural(count: number, noun: string): string {
  return `${count.toLocaleString()} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * Says that some of what is on screen was never converted.
 *
 * `foldLeaves` converts a leaf through its `pair`, so a leaf with no pair is
 * added up **as though it were dollars** — for a BRL fleet, overstating it by
 * the whole BRL/USD rate. The pair is not always recoverable: a
 * controller-performance row carries no top-level pair, only the ones inside
 * its open positions, so a controller that stopped flat has nothing left to
 * say what it traded.
 *
 * The answer is the one `ArchivedBotDetail`'s `ConversionNote` already gives:
 * fold at face value and disclose it. Never guess a quote from a bot's name,
 * and never pass the total off as dollars in silence. Drawn wherever a scope's
 * numbers are, which is why it is a component rather than a line in one header.
 */
function UnpricedNote({ leaves }: { leaves: PerfLeaf[] }) {
  if (leaves.length === 0) return null;
  return (
    <span
      className="block truncate text-[10px] text-amber-500/90"
      title={`These stopped holding no position, so the record no longer says which pair they traded: ${leaves
        .map((leaf) => leaf.label)
        .join(", ")}`}
    >
      {plural(leaves.length, "record")} named no quote currency — counted at face value, not
      converted
    </span>
  );
}


// ── Scope (READ: what the browser is currently reporting on) ──
//
// The sidebar is a *scope picker*, not a controller list: the same panes
// describe one executor, one controller, or everything the bubbles above the
// tree have left in scope. Everything downstream — the header, the KPI row, the
// chart, the positions table, whether a config editor is meaningful at all — is
// derived from one node of the tree, so there is no second notion of "what is
// selected".
//
// A scope is a `PerfNode` id (`all`, `ctrl:k`, `exec:id`), which lives in the
// URL so a scope is linkable and survives a reload (FEAT-084). What used to sit
// between `all` and `ctrl:` — a `bot:`/`type:` grouping row — is a filter now,
// so a stale link naming one lands on the fleet (see `fallbackChain`).

const FLEET_SCOPE = "all";

/** Held still, so an absent fleet map is not a new array on every render. */
const EMPTY_OWNERS: FleetOwner[] = [];

/**
 * How many finished runs are warmed when the reader switches to Terminated,
 * and how many walks run at once.
 *
 * Small on purpose. These are real upstream walks against an API that is also
 * serving the live fleet, and the reader who arrives at Terminated is almost
 * always looking at the most recent few — warming the whole history would pay
 * for 137 runs to answer a question about three.
 */
const WARM_RUNS = 6;
const WARM_CONCURRENCY = 2;

/**
 * How long the shared-performance capability answer stays fresh (FEAT-087).
 *
 * It is a property of the API build, so it changes only when the server's image
 * is rebuilt or pulled — half an hour is generous on both sides: rare enough
 * that it is one request per session in practice, short enough that an upgrade
 * either way is picked up without reloading the tab.
 */
const PERF_CAPABILITY_STALE_MS = 30 * 60_000;

/**
 * How long one executor's sampled series stays fresh, and how far it walks.
 *
 * The dump cadence upstream is 60s, so re-asking faster than that can only
 * return the same rows. Two pages is two thousand rows for one executor, which
 * at any interval on the ladder is far more instants than a chart can draw —
 * the cap exists so a pathological history cannot turn one selection into an
 * unbounded sequence of round-trips, not because it is expected to bind.
 */
const EXEC_HISTORY_STALE_MS = 60_000;
const EXEC_HISTORY_MAX_PAGES = 2;

/** Which of the bottom band's two occupants is showing, if either. */
type BandKey = "positions" | "executors" | null;

/**
 * How far back a terminated fold reaches, and how long that is in ms.
 *
 * Only the terminated population offers this: a live controller's window is
 * its deploy time, which it already reports, and cutting a live fold at a week
 * would report a PnL its own Realized tile disagrees with.
 */
const PERIODS = { "1W": 7, "1M": 30, "3M": 90, All: 0 } as const;
type PeriodKey = keyof typeof PERIODS;

/**
 * The granularities the tree can be drawn at, and what each strip segment says.
 *
 * A strip rather than a bubble row: the bubble groups are multi-select, and
 * these three are one answer — the same shape the period strip below it has,
 * for the same reason.
 */
const GRAINS: ReadonlyArray<{ key: Grain; label: string; hint: string }> = [
  { key: "both", label: "Both", hint: "Every record: controllers, with the executors under them." },
  {
    key: "controllers",
    label: "Controllers",
    hint: "Controller records only — each row reports its own numbers, with no executors under it.",
  },
  {
    key: "executors",
    label: "Executors",
    hint: "Executors only — a controller row folds the executors that ran under it, and reports nothing of its own.",
  },
];

// ── Types ──

interface PerfBrowserProps {
  controllers: ControllerInfo[];
  /** The fleet's bots, for the actions a bot scope owns: Stop and Logs. */
  bots: BotSummary[];
  server: string;
  convert: ConvertFn;
  currencySymbol: string;
  /**
   * The fleet's performance history, already fetched by the page. A single
   * controller still loads its own finer series (see ControllerPnlChart); the
   * combined scopes fold *these* rows, so switching scope costs no extra
   * request.
   */
  snapshots?: ControllerPerformanceSnapshot[];
  /** The fleet history above stops short of the earliest deploy. */
  truncated?: boolean;
  /**
   * Every executor the page has loaded — live and archived alike, from the one
   * bounded cursor walk. The browser takes the slice each population needs.
   */
  executors?: ExecutorInfo[];
  /** How far that walk got, so the tree can say when it is only part of one. */
  paging?: ExecutorPaging;
  /** The bot runs the history knows about; only the finished ones are used. */
  runs?: BotRunInfo[];
  /**
   * The controllers those finished runs left behind (FEAT-089).
   *
   * The same `ControllerInfo` shape the live fleet arrives in, which is what
   * lets the Terminated tree be fleet → bot → controller → executor, exactly
   * like the Running one, instead of a flat bucket of closed executors.
   */
  terminatedControllers?: ControllerInfo[];
  /**
   * Who owns which of this trading (FEAT-096) — the fleet map, as fetched.
   *
   * Empty is the honest default and the whole degradation story: no agents on
   * the server, or a map that failed to load, both mean every leaf is
   * unattributed, the agent level collapses, and the page is what it was.
   */
  owners?: FleetOwner[];
  /**
   * What Condor recorded itself doing (FEAT-106) — the descriptive half.
   *
   * `null` is the honest default and degrades the same way `owners` does: no
   * deeds means nothing is attributed by record, and every unowned leaf reads
   * *Before the ledger*, which is what an install with no log can truthfully
   * say about itself.
   */
  deeds?: DeedIndex | null;
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
  /**
   * The scope this browser may never rise above (FEAT-108).
   *
   * `"all"` — the whole fleet — for `/bots`, which is what makes the page the
   * unrooted case of the same component. The agent workspace roots it at
   * `agent:{runKey}`, and that root is a **floor**: it is the scope an absent
   * parameter means, the scope `Select all` selects, the node the sidebar is
   * drawn from, and the clamp every other scope is resolved against
   * (`clampScope`). A default would be escapable in four different ways; a
   * floor is not escapable at all.
   *
   * The *tree* is still built over every leaf. The fold at the root is the
   * root's, but the filter bubbles still count the whole population, because
   * "this agent's $64 out of the fleet's $210" is a sentence only a browser
   * that still knows the fleet's number can say.
   */
  rootScope?: string;
  /**
   * Which query parameter carries the scope. `"scope"` for `/bots`.
   *
   * A host whose URL already means something else by `?scope=` says so instead
   * of the two grammars fighting over one key — and because the page's key never
   * changes, every `/bots?scope=` link, notification payload and bookmark keeps
   * working with no redirect.
   */
  param?: string;
  /**
   * The run to narrow to, when the host is the one that knows it (FEAT-108).
   *
   * `undefined` leaves the browser reading `?run=` itself, which is what
   * `/bots` does and what the Lab's *see this in the fleet* link writes. The
   * workspace passes it instead: there `?run=` is the loop bar's selection and
   * has a default that is not in the URL at all, so a browser reading the URL
   * would see no run exactly when one is selected.
   */
  run?: number | null;
  /** What the run chip's × does when the host owns the run. */
  onClearRun?: () => void;
}

/**
 * How much of the executor history is actually loaded.
 *
 * The walk is capped, so the tree built from it can be a partial view of the
 * fleet — and a tree that is missing branches looks exactly like a fleet that
 * never had them. The header says which it is (see the loaded/cap notice).
 */
export interface ExecutorPaging {
  loaded: number;
  loading: boolean;
  /** The walk reached the end of the history. */
  done: boolean;
  /** The walk stopped at its page cap with more still to come. */
  capped: boolean;
  loadMore: () => void;
}

// ── A coarse wall clock ──
//
// The runtime figure and every per-hour pace derived from it are elapsed time,
// so they have to advance on their own — read once during render they would sit
// frozen until a socket frame happened to re-render the browser, and a pace
// whose divisor is stale is wrong rather than merely old.
//
// Subscribed to rather than sampled, so the read stays pure. The snapshot is
// quantised to the tick because `useSyncExternalStore` compares snapshots with
// `Object.is`: a raw `Date.now()` returns a new value on every call, including
// the several React makes within one render pass, which it answers by
// re-rendering forever.

const CLOCK_TICK_MS = 60_000;

function subscribeToClock(onChange: () => void) {
  const id = setInterval(onChange, CLOCK_TICK_MS);
  return () => clearInterval(id);
}

function clockSnapshot() {
  return Math.floor(Date.now() / CLOCK_TICK_MS) * CLOCK_TICK_MS;
}

// ── Chart sizing ──

/**
 * Everything in the chart *card* that is not one of the two panes: the header
 * strip, the two pane captions and the range strip below them. The panes are
 * sized from the space that is left, so the card fills its column exactly
 * instead of being a fixed 200px island in a screen-tall pane.
 *
 * It covers the card and nothing outside it. The strip above and the positions
 * band below are siblings of the measured element, not part of it — the chart
 * sits in a `flex-1` box between them, so growing the strip (the close-type
 * chips, FEAT-085) or opening the band shrinks that box, the ResizeObserver
 * below reports the new height, and the panes follow. Adding their heights here
 * as well would subtract them twice.
 */
const CHART_CHROME_PX = 152;
const MIN_CHART_PX = 220;

/**
 * The same subtraction for `ExecutorChart`, whose chrome is one 30px header bar
 * and a border — it draws no title, no legend and no range buttons of its own,
 * so charging it the PnL chart's allowance would waste a fifth of the box.
 */
const EXEC_CHART_CHROME_PX = 34;

function useMeasuredHeight<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const next = Math.round(entries[0].contentRect.height);
      setHeight((prev) => (Math.abs(prev - next) > 2 ? next : prev));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, height] as const;
}

// ── KPI row ──

/**
 * One headline number and the line that qualifies it.
 *
 * The sub-line is what makes the row comparable across scopes: a fleet that has
 * been up eight hours and one that has been up eight days both report a total,
 * and only the rate says which is actually earning. Modelled on the KPI strip
 * of the `bot_report` routine.
 *
 * The sub-line's height is reserved even when there is nothing to say, so the
 * tiles' baselines line up whether or not a given one has a qualifier.
 */
function Kpi({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div>
      <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">{label}</div>
      <div className="text-lg font-semibold tabular-nums leading-tight" style={color ? { color } : undefined}>
        {value}
      </div>
      <div className="mt-0.5 h-3.5 text-[10px] tabular-nums text-[var(--color-text-muted)] truncate" title={sub}>
        {sub ?? ""}
      </div>
    </div>
  );
}

/** One of the bottom band's two headers: a disclosure that is also a switch. */
function BandTab({
  label,
  open,
  onClick,
  ...rest
}: {
  label: string;
  open: boolean;
  onClick: () => void;
} & Record<`data-${string}`, unknown>) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      className={`flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider transition-colors hover:bg-[var(--color-surface-hover)] ${
        open ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      }`}
      {...rest}
    >
      <ChevronRight className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`} />
      {label}
    </button>
  );
}

// ── Component ──

export function PerfBrowser({
  controllers: controllersProp,
  bots,
  server,
  convert,
  currencySymbol,
  snapshots = [],
  truncated = false,
  executors = [],
  paging,
  runs = [],
  terminatedControllers = [],
  owners = EMPTY_OWNERS,
  deeds = null,
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
  rootScope = FLEET_SCOPE,
  param = "scope",
  run: runProp,
  onClearRun,
}: PerfBrowserProps) {
  const controllers = controllersProp;
  const cv = useCallback(
    (val: number, pair: string) => {
      const quote = pair?.split("-")[1] || "USDT";
      return convert(val, quote).value;
    },
    [convert],
  );
  const queryClient = useQueryClient();
  const [isCompact, setIsCompact] = useState(false);
  // One right-hand slot, three occupants. Config and logs used to be able to
  // fight for the same 380px column; as one value only one can win, and a
  // drawer the scope stops supporting simply stops being drawn (see
  // `openDrawer`). Closed by default: a drawer is a thing you occasionally
  // open, and the chart is the thing you came for.
  const [drawer, setDrawer] = useState<null | "config" | "logs">(null);
  // Which rows have their children drawn. An allow-list, not a deny-list: the
  // tree is flat now, so a fleet of fourteen controllers holding a hundred and
  // nineteen executors would open on all hundred and nineteen if shut had to be
  // asked for. The root is the one row that starts open.
  // The root is the one row that starts open — whichever row that is: a rooted
  // browser's first row is its root, not the fleet's (FEAT-108).
  const [open, setOpen] = useState<Set<string>>(() => new Set([FLEET_SCOPE, rootScope]));
  // The other half of the same record: which branches the reader has *closed*.
  // Needed only because bot (and agent) rows are drawn open by default (see
  // `openRows`, `AUTO_OPEN_KINDS`), so absence from `open` cannot mean shut for
  // them.
  const [shut, setShut] = useState<Set<string>>(() => new Set());
  const [showDeploy, setShowDeploy] = useState(false);
  // The editor is mounted from the first time it is opened and stays mounted
  // (hidden) after that, so its unsaved buffers survive closing it — the same
  // thing the tab it replaces did by staying mounted behind the tab bar. Two
  // flags rather than one: `mounted` never goes back down, `open` does.
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorMounted, setEditorMounted] = useState(false);
  const openEditor = useCallback(() => {
    setEditorMounted(true);
    setEditorOpen(true);
  }, []);
  // Which bot the Stop confirmation is armed for, not merely *that* it is: a
  // bare boolean stayed armed while the sidebar moved on, so the Confirm the
  // user was looking at belonged to one bot and would have stopped another.
  const [confirmStopBot, setConfirmStopBot] = useState<string | null>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const [chartRef, chartBoxH] = useMeasuredHeight<HTMLDivElement>();

  // The bottom band is a disclosure, shut until asked for (FEAT-085). It used
  // to be a third of the pane's height standing open whether or not the reader
  // wanted a breakdown, and the chart paid for it.
  //
  // It has two occupants now — the positions a scope holds and the executors
  // underneath it — and as one value only one can be open, so the band never
  // grows to fit both and the chart's height stays a function of the strip
  // alone.
  //
  // Shut on arrival every time, and deliberately not remembered: a device that
  // once opened it over a one-row controller re-opened it over a twelve-row
  // fleet, and the reader who came to read the curve was met with half a chart
  // and a table they had not asked for on this scope. Opening it is a question
  // about the scope in front of you, so it is answered per visit — the cost of
  // being wrong is one click, against a squashed chart on every load.
  const [band, setBand] = useState<BandKey>(null);
  const toggleBand = useCallback((next: Exclude<BandKey, null>) => {
    setBand((open) => (open === next ? null : next));
  }, []);

  /**
   * What the reader is looking at, as bubbles above the tree.
   *
   * These are the filters the executors page carried, split into the questions
   * a reader actually asks of a fleet — *what pair*, *what class of
   * controller*, *what kind of executor* — and they narrow the whole tree
   * rather than one table under a total that ignored them. A record's class
   * used to be a *level* of the tree instead, chosen by a `By bot / By type`
   * toggle; as a filter it combines with the others (one class across every
   * bot) and it costs the reader no chevron to walk through.
   *
   * *Which bot* is not among them: the tree draws a row per bot, and that row
   * carries the actions a bubble never could. A bubble row over the same axis
   * was a second picker stacked above the first, offering deploy-stamped names
   * too long to read — 86 of them on the terminated side — above rows that said
   * the same thing (ARCH-316).
   *
   * *Which agent* is, and for the opposite reason (FEAT-101): the tree grows an
   * agent level only where a leaf carries an owner, so on a fleet whose bots
   * are outside every strategy's namespace there is no agent row anywhere and
   * nothing says which of this trading is an agent's — or that none of it is.
   * A filter over the flat population works at every scope, in both
   * populations, and offers the half a tree level cannot: **Unattributed**.
   *
   * There were two type filters' worth of confusion in the one `types` list
   * this replaces: it matched a controller's class and an executor's type
   * against the same set, so ticking `pmm_simple` silently dropped every
   * executor and ticking `position_executor` silently dropped every controller.
   * Two lists, matched against the two vocabularies, is the fix.
   *
   * Local state, like they were — they say what the reader is looking for right
   * now, not where they are, which is what the URL carries.
   */
  const [filters, setFilters] = useState({
    pair: "",
    ctrlTypes: [] as string[],
    execTypes: [] as string[],
    agents: [] as string[],
  });
  // How far back a terminated fold reaches. It exists for the terminated
  // population alone: a live controller's runtime is its deploy, not a window
  // the reader picks.
  const [period, setPeriod] = useState<PeriodKey>("3M");

  /**
   * Which granularity of record the tree reports (ARCH-317).
   *
   * Beside `period` and `population` rather than inside `filters`, because it
   * is a *view* of the population and not a narrowing of it: it says which
   * class of record the reader is comparing, the way the toggle above it says
   * which population. That is also why it stays out of `filtersActive` and out
   * of `Clear all filters` — clearing what you searched for must not silently
   * change the granularity underneath you.
   *
   * Half of it existed already, as a side effect: ticking an executor type
   * makes the controller spine step aside so each row folds its matching
   * executors. That was the only way to ask for executors, it needed a type
   * named, and there was no way at all to ask for controllers alone.
   */
  const [grain, setGrain] = useState<Grain>("both");

  // Stopping and the detail panel belong to whichever executor is in reach,
  // which is now any scope rather than one page (FEAT-086).
  const stop = useExecutorStop(server);
  const [detail, setDetail] = useState<ExecutorInfo | null>(null);
  // The run whose archived database is open, if any. The drill-in is its own
  // view rather than a pane, because the archive is a different database with
  // its own controllers and its own history.
  const [openArchive, setOpenArchive] = useState<BotRunInfo | null>(null);
  // Which run a delete is armed for, for the reason `confirmStopBot` is a name
  // and not a boolean: the sidebar can move on while the prompt is up.
  const [confirmDeleteRun, setConfirmDeleteRun] = useState<string | null>(null);

  /**
   * Which chart a single-executor scope draws: its price, or its PnL.
   *
   * Price by default, because it is the picture that made the executors page
   * worth clicking a row on — candles with the executor's own entry, exit,
   * barriers and grid levels drawn over them, which is the only view that says
   * *why* the number in the tiles is what it is. Its PnL curve is kept a click
   * away rather than dropped: an executor records its own series since
   * FEAT-087, and for one that ran for days that curve is a real picture. For
   * the many that opened and closed inside a minute it is a flat line at zero
   * with a y-axis invented around it, which is what a fresh reader used to land
   * on.
   *
   * Held across scope changes on purpose: a reader comparing two executors is
   * comparing the same view of both.
   */
  const [execChart, setExecChart] = useState<"price" | "pnl">("price");

  const now = useSyncExternalStore(subscribeToClock, clockSnapshot, clockSnapshot);

  // The scope lives in the URL, so `?scope=ctrl:<bot>:<config id>` is a link to
  // one controller and a reload lands back on it. Written with `replace` — the
  // arrow keys walk the sidebar, and every step of that walk in the history
  // stack would make Back useless.
  const [searchParams, setSearchParams] = useSearchParams();
  // `param` is `"scope"` for `/bots` and something else for a host whose URL
  // already means something by that word; an absent parameter means the root,
  // which is the fleet for the page and the agent for the workspace (FEAT-108).
  const scopeId = searchParams.get(param) || rootScope;
  const population = parsePopulation(searchParams.get("population"));

  /** Write one view parameter, dropping it when it is the default. */
  const setParam = useCallback(
    (key: string, value: string, fallback: string) => {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev);
          if (value === fallback) params.delete(key);
          else params.set(key, value);
          return params;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );
  const setScope = useCallback(
    (next: string) => setParam(param, next, rootScope),
    [setParam, param, rootScope],
  );
  // `switchView` is declared after the tree it needs; both setters below it.

  /**
   * One run's slice of an agent's trading (FEAT-101).
   *
   * `?scope=agent:{runKey}&run=s3` is what *see this in the fleet* sends from
   * the Lab: the reader was reading run 3, and the fleet should open on run 3
   * rather than on the strategy's whole lifetime.
   *
   * It **filters, it does not re-scope**. The scope stays the agent's, so the
   * header band, the fold and the fallback chain are all the ones that were
   * already there and `resolveScope` never learns the word "run"; the run only
   * removes leaves. A `run` naming records that live on another server
   * therefore degrades to an empty scope whose band still says whose it is.
   *
   * The owner comes out of the scope id, which is why the parameter stays the
   * bare `s3`, and the ledger is read through the *same* query key the Lab's
   * run overview populates — so arriving from the Lab is a cache hit rather
   * than a second request for an answer already in hand.
   */
  const urlRun = parseRunParam(searchParams.get("run"));
  const runNum = runProp === undefined ? urlRun : runProp;
  // The scope's owner, or — once the reader has drilled into a controller of
  // theirs — the root's. Without the second half the run chip would vanish the
  // moment you opened one of the run's own controllers.
  const owner = useMemo(
    () => runOwner(scopeId) ?? runOwner(rootScope),
    [scopeId, rootScope],
  );
  const runQueryOn = runNum !== null && owner !== null;
  const { data: runLedger } = useQuery({
    queryKey: ["strategy-session-executors", owner?.slug ?? "", owner?.sslug ?? "", runNum ?? 0],
    queryFn: () => api.getStrategySessionExecutors(owner!.slug, owner!.sslug, runNum!),
    enabled: runQueryOn,
  });
  // `null` until the ledger lands: a page waiting on the fetch reports its
  // whole scope rather than blinking through an empty one.
  const runFilter = useMemo(
    () => (runQueryOn ? runRecords(runLedger?.deployments) : null),
    [runQueryOn, runLedger],
  );
  const clearRun = useCallback(
    () => (onClearRun ? onClearRun() : setParam("run", "", "")),
    [onClearRun, setParam],
  );

  // ── The tree the whole page is derived from ──

  /**
   * Which bot an executor belongs to.
   *
   * The executor record does not say (see `UNATTACHED_BOT`), so attribution
   * means asking which bot was running its `controller_id` when it opened. Two
   * sources answer that, and they answer for different populations:
   *
   *  - the **live fleet**, for an executor working right now under a controller
   *    that is on screen. A config id is shared by every bot running that
   *    config, which is normally one; where it is not, a live executor is left
   *    unattached rather than credited to whichever bot was seen first.
   *  - the **run history**, for one that has closed. Matching a closed executor
   *    against the live fleet cannot be right for a population defined as what
   *    has finished, so it is looked up by run window instead — the interval
   *    each run declared the controller for (`lib/run-attribution`).
   *
   * The live map is the fallback for the second, not a competitor: an executor
   * of a bot that is *still deployed* has no closed run window to sit in.
   */
  const botByController = useMemo(() => {
    const owners = new Map<string, string | null>();
    for (const c of controllers) {
      const id = c.controller_id || c.controller_name;
      if (!id) continue;
      const known = owners.get(id);
      owners.set(id, known === undefined || known === c.bot_name ? c.bot_name : null);
    }
    return owners;
  }, [controllers]);

  const attribute = useMemo(() => buildAttributor(runWindows(runs)), [runs]);

  /**
   * The run behind each bot name, for the two things a controller record cannot
   * say: when its bot stopped, and which archive it left.
   *
   * A bot name carries its own deploy timestamp, so it is unique per run in
   * practice; where it is not, the most recent run wins, which is the one the
   * finished controllers on screen belong to.
   */
  const runByBot = useMemo(() => {
    const latest = new Map<string, BotRunInfo>();
    for (const run of runs) {
      const seen = latest.get(run.bot_name);
      if (!seen || (run.created_at ?? "") > (seen.created_at ?? "")) latest.set(run.bot_name, run);
    }
    return latest;
  }, [runs]);

  /**
   * What is in scope, which is the *only* thing the population toggle changes.
   *
   * Running is the live fleet: every controller, with the executors currently
   * working under it. Terminated is what those controllers left behind: every
   * executor that has closed, plus the runs that have finished. Both are folded
   * by the same `foldLeaves` and drawn by the same panes, so nothing is
   * measured one way while it is live and another way once it is over.
   *
   * A function of the population rather than of *the* population, because the
   * toggle needs to know what the other side's tree looks like before it
   * switches — see `switchView`.
   */
  const leavesFor = useCallback(
    (which: Population): PerfLeaf[] => {
      const all: PerfLeaf[] = [];
      const botOf = (ex: ExecutorInfo) => botByController.get(ex.controller_id) ?? UNATTACHED_BOT;
      /**
       * The run a record belongs to **and how we know**: the two links the
       * runtime enforces — the bot's namespace, and the session id a standalone
       * executor is tagged with — then, only if neither answers, the record
       * Condor kept of its own deeds (`lib/agent-attribution`, FEAT-096/106).
       *
       * Bot name first: a controller is attributed through its bot, and an
       * executor working under one inherits that answer, so the `controller_id`
       * fallback is left to the executor nobody claims — which is exactly the
       * agent-created one, whose `controller_id` *is* its session's agent id.
       */
      const agentOf = (bot: string, controllerId: string) =>
        attributionOf(owners, deeds, bot, controllerId);
      /**
       * The bot a *closed* executor hung under, by the run that opened it.
       *
       * Asked at the executor's own start time, which is the instant the
       * question is about: a position that outlived its bot's stop still
       * belongs to the run that opened it. Falls through to the live fleet for
       * an executor of a bot that is still deployed — it has no closed run
       * window to sit in — and to `(unattached)` for one that belongs to no
       * run at all, which on a real server is every hand-opened position.
       */
      const closedBotOf = (ex: ExecutorInfo, startedAt: number | null) => {
        if (!ex.controller_id) return UNATTACHED_BOT;
        const at = startedAt ?? (ex.close_timestamp > 0 ? toMs(ex.close_timestamp) : null);
        const owner = at === null ? null : attribute(ex.controller_id, at);
        return owner ?? botByController.get(ex.controller_id) ?? UNATTACHED_BOT;
      };
      if (which === "running") {
        for (const c of controllers) {
          const att = agentOf(c.bot_name, "");
          all.push(leafFromController(c, att.runKey, att.how));
        }
        for (const ex of executors) {
          if (!isExecutorActive(ex.status)) continue;
          const bot = botOf(ex);
          const att = agentOf(bot, ex.controller_id);
          all.push(leafFromExecutor(ex, bot, att.runKey, att.how));
        }
      } else {
        // The window applies to what has finished, and is measured from each
        // record's *end*: a period called "the last week" is the trading that
        // stopped in it, not the trading that started in it.
        const days = PERIODS[period];
        const cutoff = days > 0 ? now - days * 86_400_000 : 0;
        for (const ex of executors) {
          if (isExecutorActive(ex.status)) continue;
          const started = ex.timestamp > 0 ? toMs(ex.timestamp) : null;
          const bot = closedBotOf(ex, started);
          const att = agentOf(bot, ex.controller_id);
          const leaf = leafFromExecutor(ex, bot, att.runKey, att.how);
          if (cutoff && leaf.endedAt !== null && leaf.endedAt < cutoff) continue;
          all.push(leaf);
        }
        // The controllers those runs left behind. This is the spine of the
        // terminated tree, exactly as the live controllers are the spine of the
        // running one: a controller record covers every executor it ever ran,
        // including the ones that closed long before the bounded executor walk
        // reaches, so a finished bot reports what it actually did rather than
        // whatever fraction of its executors is still in the table.
        for (const ctrl of terminatedControllers) {
          const att = agentOf(ctrl.bot_name, "");
          const leaf = leafFromTerminatedController(
            ctrl,
            runByBot.get(ctrl.bot_name),
            att.runKey,
            att.how,
          );
          if (cutoff && leaf.endedAt !== null && leaf.endedAt < cutoff) continue;
          all.push(leaf);
        }
      }

      return all;
    },
    [
      controllers,
      executors,
      runByBot,
      terminatedControllers,
      botByController,
      attribute,
      owners,
      deeds,
      period,
      now,
    ],
  );

  /**
   * The class of controller behind each config id, for the two questions the
   * bubbles ask that a leaf cannot answer alone: which class a *controller* is,
   * and which class an *executor's* controller was.
   */
  const ctrlClassById = useMemo(() => {
    const classes = new Map<string, string>();
    for (const c of population === "running" ? controllersProp : terminatedControllers) {
      const id = c.controller_id || c.controller_name;
      if (id && c.controller_name) classes.set(id, c.controller_name);
    }
    return classes;
  }, [population, controllersProp, terminatedControllers]);

  /**
   * What the type bubbles class a leaf as — a **controller** class, only ever.
   *
   * The rule itself is `controllerClassOf`, in lib/perf-tree.ts where a test
   * can reach it (the ARCH-300 split); this is the map it reads bound to it.
   * A row with nothing real to choose between is not drawn at all, which is
   * what the terminated side now gets: `controller-performance` reports no
   * class on a finished run, so there is no class to pick by, and the
   * executor-type row below is the one that still works.
   */
  const classOf = useCallback(
    (leaf: PerfLeaf): string => controllerClassOf(leaf, ctrlClassById),
    [ctrlClassById],
  );

  /**
   * Narrow a population to what the bubbles asked for.
   *
   * The filters narrow the leaf set, which means they narrow the *tree*: the
   * sidebar, the strip, the chart and the rows all describe the same filtered
   * population rather than a table being filtered under a total that is not.
   *
   * Executor types are the one rule with a twist in it. A controller record
   * covers every executor it ever ran, of every type, so it cannot be narrowed
   * to one of them — asking for `grid_executor` and keeping the controller leaf
   * would report the controller's whole trading under the name of one type of
   * executor. So picking executor types reports *the executors*: the controller
   * spine drops out, and each controller row folds the matching executors
   * beneath it instead (see `buildTree`'s spine rule, which does exactly this
   * for a controller that has no leaf of its own).
   *
   * The granularity is that same rule said out loud, and it runs first: it is
   * what the reader is looking at, so a narrowing that no longer has anything
   * to narrow is skipped rather than allowed to empty the tree.
   */
  const applyFilters = useCallback(
    (all: PerfLeaf[]): PerfLeaf[] => {
      const pair = filters.pair.trim().toLowerCase();
      const { ctrlTypes, execTypes, agents } = filters;
      if (
        grain === "both" &&
        !pair && !ctrlTypes.length && !execTypes.length && !agents.length && !runFilter
      ) {
        return all;
      }
      return all.filter((leaf) => {
        if (!matchesGrain(leaf, grain)) return false;
        if (pair && !leaf.pair.toLowerCase().includes(pair)) return false;
        if (!matchesAgents(leaf, agents, deeds)) return false;
        // The run is a URL parameter rather than a bubble, but it narrows the
        // same population by the same rule, so it belongs in the same pass.
        if (!inRun(leaf, runFilter)) return false;
        if (ctrlTypes.length && !ctrlTypes.includes(classOf(leaf))) return false;
        // Ignored in `controllers` mode, where its row is not drawn either:
        // there are no executors left for it to pick between, and applying a
        // tick the reader made in another mode would leave the tree empty
        // rather than showing them the controllers they just asked for. The
        // ticks are kept, not cleared, so going back to `Both` restores them.
        if (execTypes.length && grain !== "controllers") {
          if (leaf.kind === "controller") return false;
          if (!execTypes.includes(leaf.executorType)) return false;
        }
        return true;
      });
    },
    [filters, classOf, runFilter, grain, deeds],
  );

  /** The population as it stands, and what the bubbles left of it. */
  const rawLeaves = useMemo(() => leavesFor(population), [leavesFor, population]);
  const leaves = useMemo(() => applyFilters(rawLeaves), [applyFilters, rawLeaves]);

  /**
   * What the bubble groups offer, and how big each bucket is.
   *
   * Derived from the population *before* the filters are applied, so ticking a
   * bubble never removes the other bubbles from the row it was ticked in — a
   * filter that eats its own options cannot be undone without clearing it — and
   * a count never renumbers itself as a consequence of being ticked.
   *
   * Both are alphabetical, being vocabularies rather than events.
   */
  const filterOptions = useMemo(() => {
    const tally = (key: (leaf: PerfLeaf) => string, from: PerfLeaf[]) => {
      const counts = new Map<string, number>();
      for (const leaf of from) {
        const value = key(leaf);
        if (!value) continue;
        counts.set(value, (counts.get(value) ?? 0) + 1);
      }
      return counts;
    };

    const alpha = (counts: Map<string, number>): BubbleOption[] =>
      [...counts]
        .map(([value, count]) => ({ value, label: value, count }))
        .sort((a, b) => a.label.localeCompare(b.label));

    return {
      // A controller's own class, counted over controllers — an executor
      // inherits its controller's class rather than carrying one, so counting
      // it here would report the same controller once per executor under it.
      // A controller with no class contributes nothing (see `classOf`), which
      // is what leaves this row empty, and therefore undrawn, on a terminated
      // side whose classes upstream never reported.
      ctrlTypes: alpha(tally((leaf) => (leaf.kind === "controller" ? classOf(leaf) : ""), rawLeaves)),
      execTypes: alpha(
        tally((leaf) => (leaf.kind === "executor" ? leaf.executorType : ""), rawLeaves),
      ),
      // Every leaf, controller and executor alike: the question is "whose is
      // this", and a leaf's owner is a fact about it rather than about its
      // class, so there is no double-counting to avoid here.
      agents: agentOptions(rawLeaves, deeds),
    };
  }, [rawLeaves, classOf, deeds]);

  const filtersActive =
    !!filters.pair.trim() ||
    filters.ctrlTypes.length > 0 ||
    filters.execTypes.length > 0 ||
    filters.agents.length > 0;

  /**
   * The one bot every row on screen belongs to, when there is one.
   *
   * A bot is a node you can select, and selecting it is what puts its name in
   * the header and its actions — stop, logs, open archive, delete run — beside
   * them. When the population narrows to a single bot there is no level left to
   * pick: the fleet row folds exactly that bot's records, so it is that bot's
   * report and gets that bot's buttons.
   *
   * Read off the *filtered* leaves, so typing a pair that only one bot trades
   * collapses the bot level the same way a one-bot server does.
   */
  const soloBot = useMemo(() => {
    const seen = new Set(leaves.map((leaf) => leaf.bot));
    return seen.size === 1 ? [...seen][0] : undefined;
  }, [leaves]);
  const soloRealBot = soloBot && soloBot !== UNATTACHED_BOT ? soloBot : undefined;

  /**
   * What the fleet row is called, which depends on what is under it.
   *
   * "All controllers" over a tree of executors is a total labelled as the thing
   * it is not, so the granularity has a say: in `executors` mode there is no
   * controller record anywhere in the fold, and the row says so.
   */
  const rootLabel = useCallback(
    (which: Population, bot?: string) => {
      if (bot) return shortBotName(bot);
      if (grain === "executors") {
        return which === "running" ? "All executors" : "All closed executors";
      }
      return which === "running" ? "All controllers" : "All closed";
    },
    [grain],
  );

  /**
   * The one agent every row on screen belongs to, when there is one.
   *
   * The same shape as `soloBot` and for the same reason: when everything in
   * scope has a single owner there is no level left to pick, so the fleet row
   * *is* that agent's report — header band included.
   */
  const soloAgent = useMemo(() => {
    const seen = new Set(leaves.map((leaf) => leaf.agent));
    return seen.size === 1 ? [...seen][0] : undefined;
  }, [leaves]);

  /**
   * The nesting the tree is actually built with (FEAT-107).
   *
   * Three rules, in the order they are applied and for three different reasons:
   *
   * 1. **The reader's**, out of `?groupBy=` — owner first by default, which is
   *    the axis this page is organised around.
   * 2. **The root's**, which overrides it: a rooted browser always draws the
   *    level its floor lives on, and draws it outermost, or the floor has no
   *    node to be and the workspace reports an empty fleet (FEAT-108).
   * 3. **The population's**: an axis that tells nothing in scope apart is
   *    dropped, because a fleet running a single bot would spend a chevron
   *    saying so — except the root's own, which is never dropped.
   *
   * Read off the *filtered* leaves, so typing a pair that only one bot trades
   * collapses the bot level the same way a one-bot server does.
   */
  const askedGrouping = useMemo(
    () => groupingForRoot(parseGrouping(searchParams.get("groupBy")), rootScope),
    [searchParams, rootScope],
  );
  const floorAxis = useMemo(() => rootAxis(rootScope), [rootScope]);
  const grouping = useMemo(
    () => collapseGrouping(askedGrouping, leaves, deeds, floorAxis),
    [askedGrouping, leaves, deeds, floorAxis],
  );
  const groupByBot = grouping.includes("bot");

  const tree = useMemo(
    () => buildTree(leaves, rootLabel(population, soloRealBot), { grouping, deeds }),
    [leaves, population, soloRealBot, rootLabel, grouping, deeds],
  );
  const nodes = useMemo(() => indexTree(tree), [tree]);

  // A scope whose node has gone — a bot stopped, a config removed — would
  // render an empty screen with no way back, so it re-aims at the nearest
  // ancestor that survived rather than resetting to the fleet (see
  // `resolveScope`, which reads that ancestry out of the id itself).
  //
  // Then the floor: a scope outside `rootScope`'s subtree resolves to
  // `rootScope` (FEAT-108). Both rules land in one place because they are one
  // question — which node this scope really means — and a rooted browser has to
  // answer it after the fallback rather than before, since a fallback is one of
  // the ways a scope can end up outside the root.
  const effectiveScopeId = useMemo(
    () => clampScope(tree, resolveScope(nodes, scopeId, undefined, { grouping, deeds }), rootScope),
    [tree, nodes, scopeId, rootScope, grouping, deeds],
  );

  /**
   * The node the sidebar is drawn from, which is the floor itself.
   *
   * `tree` for `/bots`, and the agent's row in the workspace — so no click in
   * the picker can reach a row outside the root, and the keyboard walk
   * (`visibleNodeIds`) cannot either.
   *
   * A root with no node is an agent that has deployed nothing, or one whose
   * last leaf a bubble just removed. It reports an *empty* scope and never the
   * fleet: falling back to the tree here would undo the clamp above through the
   * back door.
   */
  const rootNode = useMemo(() => {
    if (rootScope === FLEET_SCOPE) return tree;
    return nodes.get(rootScope) ?? emptyScopeNode(rootScope, rootScopeLabel(rootScope));
  }, [rootScope, nodes, tree]);

  const scope = useMemo(
    () => nodes.get(effectiveScopeId) ?? rootNode,
    [nodes, effectiveScopeId, rootNode],
  );

  /**
   * How big the tree is, for the button that selects all of it.
   *
   * Counts and not money: the fleet's PnL is what the row this button replaced
   * used to shout from the top of the list, and it is one click away in the
   * pane that actually has room to explain it. What the reader needs *here* is
   * how much they are about to select — and, when a filter has bitten, that the
   * list under it is shorter than it was.
   */
  const scopeCounts = useMemo(
    () => ({
      // Counted over the whole (rooted) tree rather than the root's own
      // children: with the bot level on, every controller is a grandchild.
      bots: countNodes(rootNode, "bot"),
      controllers: countNodes(rootNode, "controller"),
      executors: collectLeaves(rootNode, "executor").length,
    }),
    [rootNode],
  );

  /**
   * The one bot this scope is about, whichever way it got there.
   *
   * A bot row selects it outright; a fleet row narrowed to one bot by the
   * bubbles still *is* that bot's report, and keeps the actions it had before
   * the row came back (a single-bot fleet draws no bot level at all).
   */
  const scopeBotName =
    scope.kind === "bot" ? botOfNodeId(scope.id) : scope.kind === "fleet" ? soloRealBot : undefined;

  /**
   * The one agent this scope is about, whichever way it got there.
   *
   * An agent row selects it outright; a fleet row that the bubbles (or a
   * one-agent server) have narrowed to a single owner still *is* that agent's
   * report, and gets the same band — the same reasoning as `scopeBotName`.
   */
  const scopeAgentKey =
    scope.kind === "agent"
      ? agentOfNodeId(scope.id)
      : scope.kind === "fleet"
        ? soloAgent || undefined
        : undefined;
  const activeAgent = useMemo(
    () => (scopeAgentKey ? ownerOf(owners, scopeAgentKey) : undefined),
    [owners, scopeAgentKey],
  );
  /**
   * The declared legacy bases actually folded into this scope.
   *
   * Only these, not every name the strategy declares: the note is about what
   * the numbers on screen include, so a base that is not in scope has nothing
   * to warn about.
   */
  const scopeLegacyBots = useMemo(() => {
    const declared = activeAgent?.declaredBots ?? [];
    if (declared.length === 0) return [];
    const inScope = new Set(collectLeaves(scope, "controller").map((leaf) => leaf.bot));
    for (const leaf of collectLeaves(scope, "executor")) inScope.add(leaf.bot);
    return declared.filter((base) =>
      [...inScope].some((bot) => bot === base || bot.startsWith(`${base}-`)),
    );
  }, [activeAgent, scope]);
  /**
   * Whether the panes are reporting the whole scope — what lights the Select
   * all button. The *root's* whole scope, which is the fleet's on `/bots` and
   * the agent's in the workspace (FEAT-108).
   */
  const atRoot = effectiveScopeId === rootScope;

  /**
   * Change the population, and keep the reader where they were.
   *
   * The switch rebuilds the tree, so the node that was selected may not exist
   * in the next one. Rather than resetting to the fleet, the next tree is built
   * *first* and the scope re-aimed against it: kept when it survives, and
   * otherwise moved to the nearest surviving ancestor, with the selected node's
   * own leaf supplying the ancestry an id cannot carry on its own (an executor
   * id deliberately says nothing about where it hangs, so that the same
   * executor keeps the same id in both populations).
   *
   * The population and the scope are written in one go, because writing them
   * one at a time would route through a tree neither view describes.
   */
  const switchView = useCallback(
    (nextPopulation: Population) => {
      if (nextPopulation === population) return;
      // Built with the levels the *next* tree will actually have, not with none:
      // a re-aim resolves an id against this tree, so a level it is missing is
      // a candidate that silently cannot be landed on. Both are derived from
      // the next population's own leaves by the same rules as above.
      const nextLeaves = applyFilters(leavesFor(nextPopulation));
      const nextGrouping = collapseGrouping(askedGrouping, nextLeaves, deeds, floorAxis);
      const nextTree = buildTree(nextLeaves, rootLabel(nextPopulation), {
        grouping: nextGrouping,
        deeds,
      });
      const aimed = clampScope(
        nextTree,
        resolveScope(indexTree(nextTree), effectiveScopeId, scope.leaves[0], {
          grouping: nextGrouping,
          deeds,
        }),
        rootScope,
      );
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev);
          if (nextPopulation === "running") params.delete("population");
          else params.set("population", nextPopulation);
          if (aimed === rootScope) params.delete(param);
          else params.set(param, aimed);
          return params;
        },
        { replace: true },
      );
    },
    [
      population,
      leavesFor,
      applyFilters,
      rootLabel,
      askedGrouping,
      floorAxis,
      deeds,
      effectiveScopeId,
      scope,
      setSearchParams,
      param,
      rootScope,
    ],
  );
  const setPopulation = switchView;

  /**
   * The finished run this scope's chart is about, if any.
   *
   * A bot scope is the run itself; a controller or executor scope under it
   * inherits the run its bot belongs to, which is what lets one cached walk
   * serve every scope inside a run. `(unattached)` has no run and no curve —
   * those executors belong to no deployment, so their outcomes are all there is.
   */
  const scopeRun = useMemo(() => {
    if (population !== "terminated") return undefined;
    const bot = scope.kind === "fleet" ? soloRealBot : scope.leaves[0]?.bot;
    return bot ? runByBot.get(bot) : undefined;
  }, [population, scope, soloRealBot, runByBot]);

  /**
   * That run's sampled history, walked once by Condor and cached for ever.
   *
   * `staleTime: Infinity` is not a tuning choice: a finished run's history is
   * immutable — the bot has stopped, and nothing that happens later can change
   * what the curve was — so a refetch could only ever return the same bytes.
   * The long `gcTime` is the same fact said to the other end of the cache:
   * clicking down a list of runs and back should not re-walk the first one.
   */
  const { data: runHistory, isFetching: runHistoryLoading } = useQuery({
    queryKey: ["run-history", server, scopeRun?.bot_name, scopeRun?.created_at],
    queryFn: () =>
      api.getRunHistory(
        server,
        scopeRun!.bot_name,
        scopeRun!.created_at!,
        scopeRun!.archive_db_path,
      ),
    enabled: !!scopeRun?.bot_name && !!scopeRun?.created_at,
    staleTime: Infinity,
    gcTime: 60 * 60_000,
    retry: false,
  });

  /**
   * Warm the runs the reader is most likely to open next.
   *
   * The first open of a cold run costs one walk — 1.7s for a three-day,
   * three-controller run measured against a real server — and the reader
   * usually arrives at Terminated intending to look at the most recent few. So
   * those are fetched in the background, two at a time, as soon as the
   * population is selected: bounded because these are real upstream walks
   * against an API that is also serving the live fleet, and never on the path
   * of the tree, which renders from the controllers listing regardless.
   */
  useEffect(() => {
    if (population !== "terminated" || !server) return;
    const newest = runs
      .filter((run) => !run.is_live && run.created_at && run.controller_ids?.length)
      .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
      .slice(0, WARM_RUNS);

    let cancelled = false;
    void (async () => {
      for (let i = 0; i < newest.length; i += WARM_CONCURRENCY) {
        if (cancelled) return;
        await Promise.all(
          newest.slice(i, i + WARM_CONCURRENCY).map((run) =>
            queryClient
              .prefetchQuery({
                queryKey: ["run-history", server, run.bot_name, run.created_at],
                queryFn: () =>
                  api.getRunHistory(server, run.bot_name, run.created_at!, run.archive_db_path),
                staleTime: Infinity,
                gcTime: 60 * 60_000,
              })
              // A warm that fails costs nothing: the scope that needs it will
              // ask again and show its own spinner and its own error.
              .catch(() => {}),
          ),
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [population, server, runs, queryClient]);

  const scopedLeaves = scope.leaves;
  const activeCtrl =
    scope.kind === "controller" && scope.leaves[0]?.kind === "controller"
      ? (scope.leaves[0].source as ControllerInfo)
      : undefined;
  /**
   * A controller that can still be acted on — i.e. a live one.
   *
   * The Terminated population selects controllers too since FEAT-089, and its
   * records look identical: same `ControllerInfo`, same node kind. But the bot
   * behind one of them is stopped and usually archived, so Pause would post a
   * kill switch to a container that is gone and Config would offer to edit a
   * deployment that has already run. Both are drawn off this rather than off
   * `activeCtrl`, so a finished controller is a *report* and nothing else.
   */
  const liveCtrl = population === "running" ? activeCtrl : undefined;
  const activeExec =
    scope.kind === "executor" ? (scope.leaves[0]?.source as ExecutorInfo | undefined) : undefined;

  /**
   * The one executor as `ExecutorChart` takes it: a list, held still.
   *
   * The chart keys its overlay rebuild off what is drawn rather than off array
   * identity, but its candle query and its layout effects still see the prop —
   * and this component re-renders on every clock tick and every WS frame, so a
   * `[activeExec]` minted inline would hand it a new group each time.
   */
  const execChartGroup = useMemo(() => (activeExec ? [activeExec] : []), [activeExec]);
  /**
   * Whether the price view can be drawn at all.
   *
   * Candles are fetched by connector and pair, so an executor carrying neither
   * — an archived row that kept only its id and its PnL — has no price chart to
   * offer, and the toggle that would offer it is not drawn either.
   */
  const execHasPrice = !!activeExec?.connector && !!activeExec?.trading_pair;

  /**
   * The run the terminated fleet row is reporting on.
   *
   * A finished run has no node of its own — the `runs` branch it used to hang
   * under went in FEAT-089, and the bot node that replaced it went with the
   * grouping level — so the two actions that belong to a run, opening its
   * archive and deleting it, belong to the fleet row *when that row is exactly
   * one run*. Undefined for `(unattached)`, which is not a run and has neither.
   */
  const activeRun =
    population === "terminated" && scopeBotName ? runByBot.get(scopeBotName) : undefined;

  /** The executors under this scope, whatever level it sits at. */
  const scopedExecutors = useMemo(
    () => collectLeaves(scope, "executor").map((leaf) => leaf.source as ExecutorInfo),
    [scope],
  );

  /**
   * The executor this scope is, when it is one — the subject of its own series.
   *
   * Until FEAT-087 an executor scope had no series and borrowed its parent
   * controller's, because upstream stored an executor as one mutable row
   * updated in place. `executor_performance_snapshots` records it over time
   * now, so the curve under an executor's name is the executor's own or there
   * is none; a controller's line captioned with an executor's name was the same
   * picture asserting something false.
   */
  const execScopeId = useMemo(() => {
    if (scope.kind !== "executor") return undefined;
    const leaf = scope.leaves[0];
    return leaf?.kind === "executor" ? leaf.id : undefined;
  }, [scope]);

  /**
   * The interval this executor's series is asked for.
   *
   * The same PERF-238 choice the fleet history makes, sized from the scope's
   * own span rather than from the fleet's: an executor that ran for four
   * minutes and one that ran for four days are the same chart width, and the
   * ladder is what keeps both inside the point budget. Reused rather than
   * reinvented, because a coarse interval silently drops rows on this route the
   * same way it does on the controller one.
   */
  const execInterval = useMemo(() => {
    const leaf = scope.kind === "executor" ? scope.leaves[0] : undefined;
    return scopeInterval(leaf?.startedAt, leaf?.endedAt);
  }, [scope]);

  /**
   * Whether this server serves `/performance/history` at all.
   *
   * One query per server, not per chart: the answer is a property of the API
   * build, so it is keyed on the server alone and every scope the reader clicks
   * through reads the same cache entry. Condor's route caches it again on its
   * own side, so even a cold browser costs one upstream request.
   *
   * `retry: false` because a "no" here is an answer, not a failure to retry.
   */
  const { data: perfCapability } = useQuery({
    queryKey: ["perf-capability", server],
    queryFn: () => api.getPerformanceCapability(server),
    enabled: !!server,
    staleTime: PERF_CAPABILITY_STALE_MS,
    gcTime: PERF_CAPABILITY_STALE_MS * 2,
    retry: false,
  });

  /**
   * This executor's own sampled series, where the server can serve one.
   *
   * Not fetched at all when the probe says the route is absent — the fallback
   * is drawn instead, and a request that is known to 404 is one nobody should
   * pay for. A closed executor's series is complete in a single query: its
   * terminal row *is* the final point, written transactionally at completion,
   * so nothing has to append a last value afterwards.
   */
  const {
    data: execHistory,
    isFetching: execHistoryLoading,
    isError: execHistoryError,
  } = useQuery({
    queryKey: ["perf-history", server, "executor", execScopeId, execInterval],
    queryFn: () =>
      api.getPerformanceHistory(
        server,
        { subject: "executor", executor_id: execScopeId!, interval: execInterval },
        { maxPages: EXEC_HISTORY_MAX_PAGES },
      ),
    enabled: !!server && !!execScopeId && perfCapability?.supported === true,
    staleTime: EXEC_HISTORY_STALE_MS,
    gcTime: 10 * 60_000,
    retry: false,
  });

  const isKilled = activeCtrl?.config?.manual_kill_switch === true;
  const isStopping = activeCtrl?.status === "stopping";

  const toggleMutation = useMutation({
    mutationFn: () =>
      isKilled
        ? api.startControllers(server, activeCtrl!.bot_name, [activeCtrl!.controller_id])
        : api.stopControllers(server, activeCtrl!.bot_name, [activeCtrl!.controller_id]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bots", server] });
    },
  });

  // ── What a single-bot scope owns: the bot itself, its logs, and stopping it ──

  const activeBot = scopeBotName ? bots.find((b) => b.bot_name === scopeBotName) : undefined;
  const botStopping = activeBot?.status === "stopping";

  // The bot is the mutation's argument rather than something it reads off the
  // current scope, so `variables` says which bot a pending or failed stop
  // belongs to — the sidebar can move on while a stop is in flight, and neither
  // the spinner nor the error should follow it to the next bot.
  const stopBotMutation = useMutation({
    mutationFn: (botName: string) => api.stopBot(server, botName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bots", server] });
      setConfirmStopBot(null);
    },
  });
  const stoppingThisBot =
    stopBotMutation.isPending && stopBotMutation.variables === activeBot?.bot_name;
  const stopFailedHere =
    stopBotMutation.isError && stopBotMutation.variables === activeBot?.bot_name;

  const stopArmed = !!activeBot && confirmStopBot === activeBot.bot_name;

  /**
   * The Stop button a bot row carries, beside its name in the sidebar.
   *
   * This is the whole point of the bot level. Stopping used to be reachable
   * only from the report header, and only once the bubbles had narrowed the
   * fleet to a single bot — a filter interaction standing in for "stop that
   * one", which nobody found. The row is where the reader already is, so the
   * button is there too, and it arms the same `confirmStopBot` the header does:
   * a bot armed from either place is armed in both, and confirming from either
   * posts once.
   *
   * Drawn only for a bot the fleet still reports as live. A terminated row's
   * deployment is gone, and a Stop that could only ever 404 is worse than none.
   */
  const renderBotAction = useCallback(
    (node: PerfNode) => {
      if (node.kind !== "bot") return null;
      const botName = botOfNodeId(node.id);
      const bot = botName ? bots.find((b) => b.bot_name === botName) : undefined;
      if (!bot) return null;

      const pending = stopBotMutation.isPending && stopBotMutation.variables === bot.bot_name;
      if (bot.status === "stopping" || pending) {
        return (
          <span
            className="h-3.5 w-3.5 animate-spin rounded-full border-[1.5px] border-[var(--color-yellow)] border-t-transparent"
            title="Stopping…"
          />
        );
      }

      if (confirmStopBot === bot.bot_name) {
        return (
          <span className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => stopBotMutation.mutate(bot.bot_name)}
              className="rounded bg-[var(--color-red)] px-1.5 py-0.5 text-[9px] font-semibold text-white transition-opacity hover:opacity-90"
              title={`Stop ${bot.bot_name} — this cancels its orders and shuts the container down`}
            >
              Stop
            </button>
            <button
              type="button"
              onClick={() => setConfirmStopBot(null)}
              className="rounded p-0.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
              title="Cancel"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        );
      }

      return (
        <button
          type="button"
          onClick={() => setConfirmStopBot(bot.bot_name)}
          className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)]"
          title={`Stop ${bot.bot_name}`}
          aria-label={`Stop ${bot.bot_name}`}
        >
          <Square className="h-3 w-3" />
        </button>
      );
    },
    [bots, confirmStopBot, stopBotMutation],
  );

  // Deleting a finished run: irreversible, and the only door to it now that the
  // runs table is gone, so it keeps the table's arm-then-confirm.
  const deleteRunMutation = useMutation({
    mutationFn: (botRunId: number) => api.deleteBotRun(server, botRunId),
    onSuccess: (_result, botRunId) => {
      // The runs query is not the only cache that still believes in this run:
      // the Terminated tree is drawn from the controllers listing, which would
      // keep the deleted run's bot, controllers and KPIs on screen for up to a
      // poll interval (CORR-298).
      const botName = runs.find((r) => r.bot_run_id === botRunId)?.bot_name;
      dropDeletedRunQueries(queryClient, server, botName);
      setConfirmDeleteRun(null);
    },
  });
  const deleteArmed = !!activeRun && confirmDeleteRun === activeRun.bot_name;
  const deletingThisRun =
    deleteRunMutation.isPending && deleteRunMutation.variables === activeRun?.bot_run_id;

  const botLogs: BotLogEntry[] = useMemo(() => {
    if (!activeBot) return [];
    return [
      ...(activeBot.error_logs || []).map((l) => ({ ...l, log_category: "error" as const })),
      ...(activeBot.general_logs || []).map((l) => ({ ...l, log_category: "general" as const })),
    ].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
  }, [activeBot]);

  /**
   * Which drawer the current scope actually supports.
   *
   * Derived rather than reset from an effect: a `logs` drawer means nothing at
   * controller scope and a `config` drawer means nothing at fleet scope, so the
   * unsupported one is simply not drawn — and walking back to a scope that does
   * support it finds it open again, which is the preference the user expressed.
   */
  const openDrawer =
    drawer === "config" && liveCtrl ? "config" : drawer === "logs" && activeBot ? "logs" : null;

  // ── Keyboard navigation over the picker, in the order it is drawn ──

  /** Every branch the selected row hangs inside, nearest first. */
  const selectedAncestors = useMemo(
    () => ancestorChain(rootNode, effectiveScopeId).slice(1),
    [rootNode, effectiveScopeId],
  );

  /**
   * What is actually drawn open: the reader's own record, plus the branches
   * holding the selection, plus the `AUTO_OPEN_KINDS` rows they have not shut.
   *
   * A controller starts shut, so a `?scope=exec:…` link — or a scope that
   * survived a filter change by falling back to its controller — would land on
   * a row inside a branch nobody had opened: highlighted in the panes, invisible
   * in the picker, and unreachable by the arrow keys, which walk what is drawn.
   * Derived rather than written back into `open` from an effect, so the state
   * stays the reader's own record of what they opened.
   *
   * Bot (and agent) rows are the levels that start *open*, and `shut` is what
   * records closing one. Shut-by-default would mean the level cost the reader a
   * click per bot before they saw a single controller — the exact charge that
   * retired the old grouping level (see `buildTree`). The Stop button is on the
   * bot row either way, so folding a bot away loses nothing but its
   * controllers. Agent is in the set for the same reason: a bot nested under an
   * agent row (`agentFor(leaf) ?? fleet`) is otherwise marked open by
   * `autoOpenIds` and drawn nowhere, because `visibleNodeIds` stops descending
   * at the shut agent above it (CORR-322). `group` deliberately stays out — see
   * `AUTO_OPEN_KINDS`.
   */
  const openRows = useMemo(() => {
    const drawn = new Set(open);
    for (const id of autoOpenIds(nodes, shut)) drawn.add(id);
    for (const id of selectedAncestors) drawn.add(id);
    return drawn;
  }, [open, shut, nodes, selectedAncestors]);

  const toggleOpen = useCallback(
    (id: string) => {
      const isOpen = openRows.has(id);
      setOpen((prev) => {
        const next = new Set(prev);
        if (isOpen) next.delete(id);
        else next.add(id);
        return next;
      });
      setShut((prev) => {
        const next = new Set(prev);
        if (isOpen) next.add(id);
        else next.delete(id);
        return next;
      });
      // Shutting the branch that holds the selection takes the selection up
      // with it. Without this the row would be hidden and re-opened in the same
      // click by `openRows`, and the chevron would read as broken.
      if (isOpen && selectedAncestors.includes(id)) setScope(id);
    },
    [openRows, selectedAncestors, setScope],
  );

  const navItems = useMemo(() => visibleNodeIds(rootNode, openRows), [rootNode, openRows]);
  const navIdx = navItems.indexOf(effectiveScopeId);

  const goUp = useCallback(() => {
    if (navIdx > 0) setScope(navItems[navIdx - 1]);
  }, [navIdx, navItems, setScope]);

  const goDown = useCallback(() => {
    if (navIdx >= 0 && navIdx < navItems.length - 1) setScope(navItems[navIdx + 1]);
  }, [navIdx, navItems, setScope]);

  // Escape used to close the browser back onto the page behind it. There is no
  // page behind it any more — it *is* `/bots` — so only the arrows are left,
  // and they stand down while something is layered over the sidebar: an arrow
  // key pressed in the editor modal or the deploy dialog would otherwise walk
  // a scope the user cannot see.
  const modalOpen = editorOpen || showDeploy;

  useEffect(() => {
    if (modalOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      // Skip if focus is inside CodeMirror editor (contenteditable div)
      const inEditor = e.target instanceof HTMLElement && e.target.closest(".cm-editor");
      if (inEditor && !e.metaKey && !e.ctrlKey) return;
      if (e.key === "ArrowUp") { goUp(); e.preventDefault(); }
      else if (e.key === "ArrowDown") { goDown(); e.preventDefault(); }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [goUp, goDown, modalOpen]);

  // Scroll active into view
  useEffect(() => {
    const el = sidebarRef.current?.querySelector("[data-active-scope]");
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [effectiveScopeId]);

  // ── What the scope adds up to, in display currency ──
  //
  // One fold, for every kind of node: a controller, a bot, the fleet. The
  // browser used to compute `totals`, `scopeFacts` and the close-type histogram
  // as three separate passes over a `ControllerInfo[]`; they are one pass over
  // a leaf set now (lib/perf-tree), which is what lets an executor scope be
  // reported by the same strip without a second implementation of any of them.
  const totals = useMemo(() => foldLeaves(scopedLeaves, cv, now), [scopedLeaves, cv, now]);

  /**
   * A per-hour pace, or nothing.
   *
   * Nothing is the right answer for a scope with no known start: a total
   * divided by a runtime we do not have is a made-up rate, and it would be
   * indistinguishable on screen from a measured one.
   */
  const perHour = (total: number, fmt: (v: number, symbol?: string) => string) =>
    totals.hours > 0 ? `${fmt(total / totals.hours, currencySymbol)}/hr` : undefined;

  const positionRows = useMemo<PositionRow[]>(
    () => buildPositionRows(scopedLeaves, cv),
    [scopedLeaves, cv],
  );

  // ── The aggregated series, folded from the fleet history the page already has ──

  /**
   * The live controllers the chart draws — the scope's own, always.
   *
   * It used to be "the scope's own, or its parent's when the scope is an
   * executor". That inheritance is gone with FEAT-087: an executor draws its
   * own recorded series or none.
   */
  const scopedControllers = useMemo(
    () =>
      scopedLeaves
        .filter((leaf: PerfLeaf) => leaf.kind === "controller")
        .map((leaf) => leaf.source as ControllerInfo),
    [scopedLeaves],
  );

  const scopedKeys = useMemo(
    () => new Set(scopedControllers.map((c) => controllerKey(c))),
    [scopedControllers],
  );

  /**
   * Where this scope's series comes from.
   *
   * The decision itself lives in `lib/perf-history.ts` — this is the seam
   * FEAT-086 named and FEAT-087 swapped at. What is computed here is only the
   * three *candidates*, in the shape that module resolves between: the
   * upstream sampled rows for an executor scope, the controller-history fold
   * for everything else, and the closed outcomes as the last resort.
   */
  /**
   * A scope narrower than the run, on a run only its archive remembers.
   *
   * The archived database computes one curve for the whole run out of its trade
   * table; it has no per-controller breakdown and cannot be given one without
   * inventing a split nobody recorded. So a controller or executor scope on
   * such a run draws nothing and says why, rather than showing the run's curve
   * under a controller's name — which would be the same picture asserting
   * something false.
   */
  const archiveOnlyController =
    runHistory?.source === "archive" &&
    (scope.kind === "controller" || scope.kind === "executor");

  /** The controller-history candidate: what this scope drew before FEAT-087. */
  const controllerPoints = useMemo(() => {
    // A *live* controller draws its own finer series (see `ControllerPnlChart`).
    // A finished one does not: its curve is already in the run's cached history,
    // over the run's real window rather than deploy-to-now, and folding it here
    // means selecting a controller costs nothing and cannot disagree with the
    // bot scope above it.
    if (activeCtrl && population === "running") return [];
    if (population === "terminated") {
      // The run's own sampled history, folded by the *same* function the live
      // fleet's is (FEAT-089). `executorSeries` is the fallback rather than the
      // rule now: it draws each close at its close time, which is the only
      // thing available for executors that belong to no run — and for a run
      // whose history the server never recorded.
      if (runHistory && runHistory.points > 0) {
        if (archiveOnlyController) return [];
        const expanded = snapshotsFromRunHistory(runHistory, scopeRun?.bot_name ?? "");
        // An archive-derived curve describes the run as a whole and is filed
        // under a reserved id, so the scope's own controller keys would drop it
        // entirely. Enabling exactly the keys the payload carries is what makes
        // "the run" drawable without pretending it belongs to a controller.
        const keys =
          runHistory.source === "archive"
            ? new Set(expanded.map((snap) => controllerKey(snap)))
            : scopedKeys;
        // No live controllers, deliberately. `aggregatePnlSeries` ends a series
        // with a "now" point read off the controllers it is given, so that a
        // live chart reaches real time rather than stopping at the last stored
        // snapshot. A run that stopped last week has no "now": handing its
        // records over would draw a flat line from its final trade to this
        // instant, which reads as a bot still holding a position. The curve
        // ends where the trading ended.
        return aggregatePnlSeries(expanded, keys, [], convert);
      }
      // Nothing sampled: the outcome fold is the fallback, and
      // `resolvePerfSeries` reaches it because this candidate came back empty.
      return [];
    }
    return aggregatePnlSeries(snapshots, scopedKeys, scopedControllers, convert);
  }, [
    activeCtrl, population, snapshots, scopedKeys, scopedControllers,
    convert, runHistory, scopeRun, archiveOnlyController,
  ]);

  /**
   * The one place that decides which of the three sources this scope draws.
   *
   * The candidates are ordered by strength inside `resolvePerfSeries`, not
   * here: upstream snapshots, then the controller history, then the closes.
   * It also reports *why* a fallback was taken, which is the difference
   * between "this scope has no history" and "this server cannot record one".
   */
  const series = useMemo(
    () =>
      resolvePerfSeries({
        snapshots: execHistory?.supported === false ? undefined : execHistory?.snapshots,
        controllerPoints,
        // Only the terminated population has closes to fold. A running scope
        // whose executors have all closed is a contradiction the tree does not
        // produce, and offering the fold there would draw a "closed outcomes"
        // curve under a live controller.
        outcomes: population === "terminated" ? scope.leaves : undefined,
        supported: perfCapability?.supported,
        convert,
        cv,
      }),
    [execHistory, controllerPoints, population, scope, perfCapability, convert, cv],
  );
  const chartData = series.points;

  /**
   * Leaves in scope whose quote currency is unknown, and that traded.
   *
   * `foldLeaves` converts through `leaf.pair`, and a leaf with no pair falls to
   * the default quote — i.e. it is added up **as though it were dollars**. For a
   * BRL fleet that overstates every figure it touches by the whole BRL/USD
   * rate, which is the exact failure `ArchivedBotDetail`'s `ConversionNote`
   * exists to disclose.
   *
   * It is not always recoverable: a controller-performance row carries no
   * top-level pair, only the ones inside its open positions, so a controller
   * that stopped flat has nothing left to say what it traded. Measured on a
   * real server, 13 of 102 finished controllers with trading activity are in
   * that position, and all 13 are BRL. The honest answer is to fold them at
   * face value and *say so* — never to guess a quote from a bot's name, and
   * never to pass the total off as dollars in silence.
   *
   * Zero-valued leaves are excluded: a controller with nothing to convert
   * cannot be misconverted, and warning about it would bury the cases that
   * matter under the ones that do not.
   */
  const unpricedLeaves = useMemo(
    () =>
      scopedLeaves.filter(
        (leaf) => !leaf.pair && (leaf.net !== 0 || leaf.volume !== 0),
      ),
    [scopedLeaves],
  );

  /**
   * What this scope folds, in words.
   *
   * Terminated folds two kinds since FEAT-089: the controllers a finished run
   * left behind, which are its spine, and the closed executors that belong to
   * no run at all. A scope holding both is described by neither noun alone, so
   * it is named for what is actually in it rather than for its population.
   *
   * Declared here rather than beside the header it labels, because the facts
   * block below reads it too and a hook must not close over a binding a later
   * early return can leave uninitialised.
   */
  const scopeNoun =
    population === "running"
      ? grain === "executors"
        ? "executor"
        : "controller"
      : !scopedLeaves.some((leaf) => leaf.kind === "executor")
        ? "finished controller"
        : scopedLeaves.some((leaf) => leaf.kind === "controller")
          ? "finished record"
          : "closed executor";

  /**
   * Tell the chat what is actually on screen (FEAT-059, FEAT-060).
   *
   * Every one of these is a choice the reader made that no cache holds: which
   * population, which node is selected, what the bubbles left in it and how far
   * back the window reaches. Without them the block invites an answer about the
   * whole fleet derived from a filtered slice of one population of it.
   */
  useViewFacts(() => {
    /** `executor type a/b`, or `… 7 selected` once a list stops being readable. */
    const picked = (noun: string, values: string[]) =>
      values.length === 0
        ? ""
        : values.length <= 3
          ? `${noun} ${values.join("/")}`
          : `${noun} ${values.length} selected`;
    const chips = [
      filters.pair.trim() ? `pair ~ "${filters.pair.trim()}"` : "",
      picked("controller type", filters.ctrlTypes),
      picked("executor type", filters.execTypes),
      // Named the way the bubbles are, so the chat reads the same words the
      // reader ticked rather than the run keys underneath them.
      picked(
        "agent",
        filters.agents.map((value) => agentBucketLabel(value).toLowerCase()),
      ),
      // The run is a filter like any other, and one the reader did not tick —
      // it arrived in a link — so it is the one that most needs saying.
      runNum !== null ? runChipLabel(runNum) : "",
    ].filter(Boolean);

    const subject = activeCtrl
      ? `controller "${activeCtrl.controller_name}" (${activeCtrl.trading_pair}) of bot ${activeCtrl.bot_name}`
      : activeExec
        ? `executor ${activeExec.id} (${activeExec.type}, ${activeExec.trading_pair})`
        : activeRun
          ? `the finished run of bot ${activeRun.bot_name}`
          : scopeAgentKey && scope.kind === "agent"
            ? `${plural(scope.leaves.length, scopeNoun)} operated by agent ${ownerTitle(owners, scopeAgentKey)}`
            : scopeBotName
              ? `${plural(scope.leaves.length, scopeNoun)} of bot ${scopeBotName}`
              : scope.kind === "fleet"
                ? `all ${plural(scope.leaves.length, scopeNoun)} in scope`
                : `${plural(scope.leaves.length, scopeNoun)} under ${scope.kind} "${scope.label}"`;

    return {
      // The same label the route entry uses, so the cache's half of this screen
      // and the reader's half render as one screen rather than two.
      label: "Bots",
      subject,
      onScreen: {
        population,
        // Which class of record these numbers are, beside which population they
        // came from: a fold of executors and a fold of the controllers that ran
        // them are two different reports, and the block that invites an answer
        // about "the fleet" has to say which one it is quoting.
        granularity: grain,
        scope: effectiveScopeId,
        // Named rather than left as a `agent:` id, and with the one fact only
        // this scope carries: whether the loop behind these numbers is alive.
        agent: scopeAgentKey
          ? `${ownerTitle(owners, scopeAgentKey)} (${[
              loopStatus(activeAgent?.live),
              ...loopFacts(activeAgent?.live, Date.now()),
            ].join(", ")})`
          : undefined,
        // Said either way round: "none" is a fact about the tree, and leaving
        // it out reads as "filters unknown" rather than "showing everything".
        filters: chips.length ? chips.join(", ") : "none",
        window: population === "terminated" ? period : undefined,
        "in scope": scope.leaves.length,
        "executors loaded": paging
          ? paging.capped
            ? `${paging.loaded} of more — not all loaded`
            : `${paging.loaded} of ${paging.loaded}`
          : undefined,
      },
    };
  });

  const configId = activeCtrl ? activeCtrl.controller_id || activeCtrl.controller_name : "";
  const chartHeight = Math.max(MIN_CHART_PX, (chartBoxH || 420) - CHART_CHROME_PX);
  const execChartHeight = Math.max(MIN_CHART_PX, (chartBoxH || 420) - EXEC_CHART_CHROME_PX);

  // The title names the scope, always. It used to name the *parent controller*
  // for an executor scope, because that was whose curve was drawn; an executor
  // draws its own now, so there is no one else to name.
  const chartTitle =
    scope.kind === "fleet"
      ? population === "terminated"
        ? "Closed PnL"
        : "Fleet PnL"
      : `${scope.kind === "bot" ? shortBotName(scope.label) : scope.label} PnL`;
  // Where the curve came from, said rather than assumed. The selection is pure
  // over these facts, so it lives in lib/perf-notices.ts where a test can
  // reach it (ARCH-300); what stays here is only the reading of the facts.
  // `server_online` travels with the response because Condor's route reports an
  // unreachable upstream in band rather than as an HTTP error (CORR-299).
  const notice = chartNotice({
    scopeKind: scope.kind,
    population,
    runHistory,
    seriesSource: series.source,
    capabilitySupported: perfCapability?.supported,
    execHistoryLoading,
    execHistoryError,
    execHistoryServerOnline: execHistory?.server_online,
    execHistoryErrorHint: execHistory?.error_hint,
    truncated,
  });

  return (
    <div className="flex h-full min-h-0 bg-[var(--color-bg)]">
      {/* Left sidebar: the scope picker */}
      <div
        className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
          isCompact ? "w-12" : "w-72"
        }`}
      >
        {/* Header: what is in scope, and what has been narrowed out of it.

            Everything here describes the *tree* rather than the report — the
            panes to the right are the same panes whichever way these are set,
            which is the point (FEAT-086). The bubbles are where the `By bot /
            By type` toggle used to be, and they do the job it did without
            spending a level of the tree on it. */}
        <div className="shrink-0 border-b border-[var(--color-border)]">
          <div className="flex items-center justify-between px-3 py-2.5">
            {!isCompact && (
              <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                Scope
              </span>
            )}
            <button
              onClick={() => setIsCompact(!isCompact)}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              title={isCompact ? "Expand sidebar" : "Collapse sidebar"}
            >
              {isCompact ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
            </button>
          </div>
          {/* Capped and scrollable: three expanded bubble groups over a
              hundred-run history would otherwise push the tree off a short
              window, and the tree is the thing the sidebar is for. */}
          {!isCompact && (
            <div className="flex max-h-[45vh] flex-col gap-1.5 overflow-y-auto scrollbar-thin px-2 pb-2">
              <PopulationToggle population={population} onChange={setPopulation} />

              {/* Which granularity of record the tree draws (ARCH-317). One of
                  the panel's questions rather than one of its filters — the
                  population says *whose* records, the bubbles say *what class*,
                  and this says which *kind of thing* is being compared. A strip
                  and not a bubble row because the three are one answer. */}
              <div className="flex gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
                {GRAINS.map(({ key, label, hint }) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setGrain(key)}
                    aria-pressed={grain === key}
                    title={hint}
                    className={`flex-1 rounded px-1 py-1 text-[10px] font-medium transition-colors ${
                      grain === key
                        ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                        : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/* The window a terminated fold reaches back over. Offered here
                  and not for the live fleet, whose window is its own deploy —
                  cutting a live fold at a week would report a PnL that its
                  Realized tile disagrees with. */}
              {population === "terminated" && (
                <div className="flex gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
                  {(Object.keys(PERIODS) as PeriodKey[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setPeriod(key)}
                      aria-pressed={period === key}
                      className={`flex-1 rounded px-1 py-1 text-[10px] font-medium transition-colors ${
                        period === key
                          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                          : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                      }`}
                    >
                      {key}
                    </button>
                  ))}
                </div>
              )}

              {/* The executors page's filters, now narrowing the tree itself
                  rather than one table under a total that ignored them. */}
              <input
                type="text"
                value={filters.pair}
                onChange={(e) => setFilters((f) => ({ ...f, pair: e.target.value }))}
                placeholder="Filter pair…"
                className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-[11px] transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
              />

              {/* One bubble row per question a reader asks of a fleet. Each is
                  drawn only when it has something to choose between: a
                  terminated side whose controllers all report the same
                  (unknown) class is not a filter. *Which bot* is missing on
                  purpose — the tree below draws a row per bot, and that row can
                  be acted on (ARCH-316). */}
              {filterOptions.ctrlTypes.length > 1 && (
                <BubbleGroup
                  // "Controller type" rather than "Type": the row under it is
                  // "Executor type", and two rows of bubbles that look alike
                  // need their names to say which is which.
                  title="Controller type"
                  hint="The class each controller is — pmm_simple, grid_strike, and so on. Executors are narrowed by the class of the controller that ran them; to pick them by what they are, use Executor type."
                  options={filterOptions.ctrlTypes}
                  selected={filters.ctrlTypes}
                  onChange={(v) => setFilters((f) => ({ ...f, ctrlTypes: v }))}
                />
              )}
              {/* Not drawn in `Controllers` mode: there are no executors in the
                  tree to pick between. `Controller type` stays in all three —
                  it classes an executor by the controller that ran it, so it
                  narrows whatever is on screen. */}
              {grain !== "controllers" && filterOptions.execTypes.length > 1 && (
                <BubbleGroup
                  title="Executor type"
                  hint="Picking one reports the executors themselves: a controller record covers every type it ever ran, so it steps aside and each row folds the matching executors instead."
                  options={filterOptions.execTypes}
                  selected={filters.execTypes}
                  onChange={(v) => setFilters((f) => ({ ...f, execTypes: v }))}
                />
              )}
              {/* Whose this trading is. Third rather than first: the two above
                  it are about *what* a record is, which is what a reader
                  narrows by most days, and this one answers a question the
                  tree already answers wherever it has grown an agent level. */}
              {filterOptions.agents.length > 1 && (
                <BubbleGroup
                  title="Agent"
                  hint="Which agent's strategy owns each record — by the bot's namespace, or by the session an executor was tagged with. Unattributed is everything the fleet map credits to nobody, which on most servers is nearly all of it."
                  options={filterOptions.agents}
                  selected={filters.agents}
                  onChange={(v) => setFilters((f) => ({ ...f, agents: v }))}
                />
              )}
              {filtersActive && (
                <button
                  type="button"
                  onClick={() => setFilters({ pair: "", ctrlTypes: [], execTypes: [], agents: [] })}
                  className="self-start text-[10px] text-[var(--color-text-muted)] underline-offset-2 hover:text-[var(--color-text)] hover:underline"
                >
                  Clear all filters
                </button>
              )}
            </div>
          )}
        </div>

        {/* Scope list: controller → executor, and nothing else.

            The fleet is the button above it rather than the first row of it.
            An "All controllers" card at the top of the list was a total dressed
            as one of the things it totalled: always first, always the biggest
            number on the column, and always in the way of the rows the reader
            opened the sidebar to compare. As a button it costs a line instead
            of a card, says how much it is about to select, and lights up when
            it is what the panes are reporting. */}
        <div ref={sidebarRef} className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
            {isCompact ? (
              <button
                onClick={() => setScope(rootScope)}
                {...(atRoot ? { "data-active-scope": true } : {})}
                aria-pressed={atRoot}
                title={`Select all — ${plural(scopeCounts.controllers, "controller")}`}
                className={`flex w-full items-center justify-center py-3 transition-colors ${
                  atRoot
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <Layers className="h-3.5 w-3.5" />
              </button>
            ) : (
              <div className="flex items-center gap-1.5 px-2 py-1.5">
                <span className="truncate text-[10px] tabular-nums text-[var(--color-text-muted)]">
                  {scopeCounts.bots > 0 && `${plural(scopeCounts.bots, "bot")} · `}
                  {plural(scopeCounts.controllers, "controller")}
                  {scopeCounts.executors > 0 && ` · ${plural(scopeCounts.executors, "executor")}`}
                </span>
                <button
                  type="button"
                  onClick={() => setScope(rootScope)}
                  aria-pressed={atRoot}
                  {...(atRoot ? { "data-active-scope": true } : {})}
                  title="Report on everything in scope, combined"
                  className={`ml-auto flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition-colors ${
                    atRoot
                      ? "border-[var(--color-primary)] bg-[var(--color-primary)]/15 font-semibold text-[var(--color-primary)]"
                      : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-text)]"
                  }`}
                >
                  <Layers className="h-3 w-3 shrink-0" />
                  Select all
                </button>
              </div>
            )}
          </div>
          <ScopeTree
            root={rootNode}
            activeId={effectiveScopeId}
            open={openRows}
            showBot={!soloBot && !groupByBot}
            onSelect={setScope}
            onToggleOpen={toggleOpen}
            cv={cv}
            currencySymbol={currencySymbol}
            now={now}
            compact={isCompact}
            renderAction={isCompact ? undefined : renderBotAction}
          />
        </div>

        {/* How much of the executor history the tree was built from.
            A tree missing branches looks exactly like a fleet that never had
            them, so a capped walk has to say so here — the cap now bounds the
            *sidebar*, not just a table's row count (FEAT-086). */}
        {!isCompact && paging && paging.loaded > 0 && (
          <div className="shrink-0 border-t border-[var(--color-border)] px-3 py-1.5 text-[10px] text-[var(--color-text-muted)]">
            <div className="flex items-center gap-1.5">
              <span className="tabular-nums">{paging.loaded.toLocaleString()} executors loaded</span>
              {paging.loading && <span>· loading…</span>}
              {paging.done && <span>· all</span>}
              {paging.capped && (
                <button
                  onClick={paging.loadMore}
                  className="ml-auto rounded border border-[var(--color-border)] px-1.5 py-0.5 font-medium hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                >
                  Load more
                </button>
              )}
            </div>
            {paging.capped && (
              <p className="mt-0.5 text-[var(--color-yellow)]">
                Cap reached — this tree shows part of the history, not all of it.
              </p>
            )}
          </div>
        )}

        {/* Nav hints */}
        {!isCompact && (
          <div className="border-t border-[var(--color-border)] px-3 py-2 text-[10px] text-[var(--color-text-muted)]/60">
            <span className="flex items-center gap-1.5">
              <span className="flex items-center gap-0.5">
                <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                  <ChevronUp className="h-2.5 w-2.5" />
                </kbd>
                <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                  <ChevronDown className="h-2.5 w-2.5" />
                </kbd>
                <span className="ml-0.5">navigate</span>
              </span>
            </span>
          </div>
        )}
      </div>

      {/* Main content — or an archived run's own database, opened over it.

          The archive is a different database with its own controllers and its
          own history, so it is a view rather than a pane; the sidebar stays put
          so Back is one click and the scope is still where it was. */}
      {openArchive?.archive_db_path ? (
        <div className="flex-1 min-w-0 overflow-auto p-6">
          <ArchivedBotDetail
            dbPath={openArchive.archive_db_path}
            botName={openArchive.bot_name}
            onBack={() => setOpenArchive(null)}
          />
        </div>
      ) : (
      <div className="flex flex-1 flex-col min-w-0">
        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-2.5">
          <div className="flex items-center gap-3 min-w-0">
            {activeCtrl ? (
              <>
                <div className="truncate">
                  <h2 className="text-sm font-semibold truncate">{activeCtrl.controller_name}</h2>
                  {activeCtrl.controller_id && activeCtrl.controller_id !== activeCtrl.controller_name && (
                    <span className="text-[10px] text-[var(--color-text-muted)] font-mono block truncate">
                      {activeCtrl.controller_id}
                    </span>
                  )}
                  <UnpricedNote leaves={unpricedLeaves} />
                </div>
                {activeCtrl.connector && (
                  <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] border border-[var(--color-border)]/50">
                    {activeCtrl.connector}
                  </span>
                )}
                {activeCtrl.trading_pair && (
                  <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
                    {activeCtrl.trading_pair}
                  </span>
                )}
                <div className="flex items-center gap-1.5 shrink-0">
                  <StatusDot status={isStopping ? "stopping" : isKilled ? "stopped" : activeCtrl.status} />
                  <span className="text-xs capitalize">{isStopping ? "stopping" : isKilled ? "stopped" : activeCtrl.status}</span>
                </div>
              </>
            ) : activeRun ? (
              // A finished run has no node of its own, so the header a run used
              // to get under the `runs` branch is the header the fleet row gets
              // when the bubbles have narrowed it to that one run: the same
              // name, the same provenance line, the same status. The controller
              // count comes from the tree rather than from the run record,
              // whose `num_controllers` is aggregated off the *live* fleet and
              // is therefore zero for everything in here.
              <>
                <div className="truncate">
                  <h2 className="text-sm font-semibold truncate" title={activeRun.bot_name}>
                    {shortBotName(activeRun.bot_name)}
                  </h2>
                  <span className="text-[10px] text-[var(--color-text-muted)] block truncate">
                    {[
                      activeRun.account_name,
                      activeRun.strategy_name,
                      plural(scope.children.length, "controller"),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                  <UnpricedNote leaves={unpricedLeaves} />
                </div>
                <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] border border-[var(--color-border)]/50">
                  {activeRun.deployment_status}
                </span>
                <div className="flex items-center gap-1.5 shrink-0">
                  <StatusDot status={runStatus(activeRun)} />
                  <span className="text-xs capitalize">{runStatus(activeRun) || "—"}</span>
                </div>
              </>
            ) : activeExec ? (
              <>
                <div className="truncate">
                  <h2 className="text-sm font-semibold truncate font-mono" title={activeExec.id}>
                    {activeExec.id.slice(0, 14)}…
                  </h2>
                  <span className="text-[10px] text-[var(--color-text-muted)] block truncate">
                    {activeExec.type}
                    {activeExec.controller_id ? ` · ${activeExec.controller_id}` : ""}
                  </span>
                </div>
                {activeExec.connector && (
                  <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] border border-[var(--color-border)]/50">
                    {activeExec.connector}
                  </span>
                )}
                {activeExec.trading_pair && (
                  <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
                    {activeExec.trading_pair}
                  </span>
                )}
                <div className="flex items-center gap-1.5 shrink-0">
                  <StatusDot status={activeExec.status} />
                  <span className="text-xs capitalize">
                    {activeExec.close_type ? parseSide(activeExec.close_type) : activeExec.status}
                  </span>
                </div>
              </>
            ) : scopeAgentKey ? (
              // An agent scope: the same report every other scope gets, plus
              // the one thing only this scope can say — whether the loop that
              // produced these numbers is still alive, and what it last said.
              <AgentScopeHeader
                runKey={scopeAgentKey}
                owner={activeAgent}
                legacyBots={scopeLegacyBots}
                botName={scopeBotName ?? undefined}
              >
                <span className="block truncate text-[10px] text-[var(--color-text-muted)]">
                  {plural(scopedLeaves.length, scopeNoun)} aggregated
                  {filtersActive && " · filtered"}
                </span>
                <UnpricedNote leaves={unpricedLeaves} />
              </AgentScopeHeader>
            ) : (
              // Every other scope — the whole fleet, one live bot's share of
              // it, or a controller reconstructed out of closed executors. One
              // header for all of them: what it is, and what it folds.
              <div className="truncate">
                <h2 className="text-sm font-semibold truncate flex items-center gap-2">
                  {scopeBotName ? (
                    <Server className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                  ) : (
                    <Layers className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                  )}
                  <span className="truncate" title={scopeBotName ?? scope.label}>
                    {scopeBotName
                      ? shortBotName(scopeBotName)
                      : scope.kind !== "fleet"
                        ? scope.label
                        : population === "running"
                          ? "All controllers combined"
                          : "Everything that has finished"}
                  </span>
                </h2>
                <span className="text-[10px] text-[var(--color-text-muted)] block truncate">
                  {plural(scopedLeaves.length, scopeNoun)} aggregated
                  {filtersActive && " · filtered"}
                </span>
                <UnpricedNote leaves={unpricedLeaves} />
              </div>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            {/* The run this scope is narrowed to, and the way back out of it.
                A filter that cannot be seen is a filter that cannot be undone,
                which is the rule the bubbles beside the tree were built on —
                and this one arrived in a link rather than being ticked. */}
            {runNum !== null && (
              <span
                className="flex shrink-0 items-center gap-1 rounded-full border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 px-2 py-0.5 text-[10px] font-medium text-[var(--color-primary)]"
                title={
                  owner
                    ? `Only the bots, controllers and executors run ${runNum} deployed`
                    : `A run narrows an agent scope; this scope is not one, so nothing is filtered`
                }
              >
                {runChipLabel(runNum)}
                <button
                  type="button"
                  onClick={clearRun}
                  aria-label="Clear the run filter"
                  className="transition-opacity hover:opacity-70"
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </span>
            )}
            <div className="flex items-center border border-[var(--color-border)] rounded overflow-hidden mr-1">
              <button
                onClick={goUp}
                disabled={navIdx <= 0}
                className="p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
                title="Previous (↑)"
              >
                <ChevronUp className="h-3.5 w-3.5" />
              </button>
              <span className="text-[10px] tabular-nums text-[var(--color-text-muted)] px-1 border-x border-[var(--color-border)]">
                {navIdx + 1}/{navItems.length}
              </span>
              <button
                onClick={goDown}
                disabled={navIdx < 0 || navIdx >= navItems.length - 1}
                className="p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
                title="Next (↓)"
              >
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
            </div>
            {/* Every action belongs to a scope, and is drawn only there: the
                fleet deploys bots, a bot is stopped and read, a controller is
                paused and configured. The accordion and the table that used to
                carry the first two are gone (FEAT-084). */}
            {scope.kind === "fleet" && (
              <button
                onClick={() => setShowDeploy(true)}
                className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-primary)] transition-colors hover:bg-[var(--color-primary)]/10"
                title="Deploy a new bot"
              >
                <Rocket className="h-3.5 w-3.5" />
                Deploy bot
              </button>
            )}

            {activeBot && (
              <>
                {botStopping ? (
                  <span className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-[var(--color-yellow)]">
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    Stopping
                  </span>
                ) : stopArmed ? (
                  <span className="flex items-center gap-1.5">
                    <button
                      onClick={() => stopBotMutation.mutate(activeBot.bot_name)}
                      disabled={stoppingThisBot}
                      className="rounded bg-[var(--color-red)] px-2 py-1 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                    >
                      {stoppingThisBot ? "Stopping..." : "Confirm"}
                    </button>
                    <button
                      onClick={() => setConfirmStopBot(null)}
                      className="rounded px-2 py-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => setConfirmStopBot(activeBot.bot_name)}
                    className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-[var(--color-red)]/10"
                    title="Stop bot"
                  >
                    <Square className="h-3.5 w-3.5" />
                    Stop bot
                  </button>
                )}
                {stopFailedHere && (
                  <span
                    className="text-[10px] whitespace-nowrap text-[var(--color-red)]"
                    title={
                      stopBotMutation.error instanceof Error
                        ? stopBotMutation.error.message
                        : "Unknown error"
                    }
                  >
                    Failed to stop
                  </span>
                )}
                <button
                  onClick={() => setDrawer((d) => (d === "logs" ? null : "logs"))}
                  className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                    openDrawer === "logs"
                      ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title={openDrawer === "logs" ? "Hide logs" : "Show logs"}
                >
                  <ScrollText className="h-3.5 w-3.5" />
                  Logs
                  {activeBot.error_count > 0 && (
                    <span className="text-[10px] text-[var(--color-yellow)]">
                      {activeBot.error_count}
                    </span>
                  )}
                </button>
              </>
            )}

            {activeRun?.archive_db_path && (
              <button
                onClick={() => setOpenArchive(activeRun)}
                className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-primary)] transition-colors hover:bg-[var(--color-primary)]/10"
                title="Open this run's archived database"
              >
                <Database className="h-3.5 w-3.5" />
                Open archive
              </button>
            )}

            {activeRun?.bot_run_id && activeRun.deployment_status === "ARCHIVED" && (
              deleteArmed ? (
                <span className="flex items-center gap-1.5">
                  <button
                    onClick={() => deleteRunMutation.mutate(activeRun.bot_run_id!)}
                    disabled={deletingThisRun}
                    className="rounded bg-[var(--color-red)] px-2 py-1 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    {deletingThisRun ? "Deleting…" : "Confirm delete"}
                  </button>
                  <button
                    onClick={() => setConfirmDeleteRun(null)}
                    className="rounded px-2 py-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  onClick={() => setConfirmDeleteRun(activeRun.bot_name)}
                  className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-[var(--color-red)]/10"
                  title="Delete this bot run permanently"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete run
                </button>
              )
            )}
            {deleteRunMutation.isError && activeRun && (
              <span
                className="text-[10px] whitespace-nowrap text-[var(--color-red)]"
                title={
                  deleteRunMutation.error instanceof Error
                    ? deleteRunMutation.error.message
                    : "Unknown error"
                }
              >
                Failed to delete
              </span>
            )}

            {/* Which picture this executor is telling its story with. Drawn
                only where there is a choice: no candles, no toggle. */}
            {activeExec && execHasPrice && (
              <div className="flex shrink-0 items-center gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5">
                {(["price", "pnl"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setExecChart(mode)}
                    aria-pressed={execChart === mode}
                    title={
                      mode === "price"
                        ? "Candles, with this executor's entry, exit and levels drawn over them"
                        : "This executor's own PnL over time"
                    }
                    className={`rounded px-2 py-1 text-[10px] font-medium transition-colors ${
                      execChart === mode
                        ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                        : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    }`}
                  >
                    {mode === "price" ? "Price" : "PnL"}
                  </button>
                ))}
              </div>
            )}

            {activeExec && isExecutorActive(activeExec.status) && (
              <button
                onClick={() => stop.request([activeExec.id])}
                disabled={stop.stoppingIds.has(activeExec.id)}
                className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-[var(--color-red)]/10 disabled:opacity-50"
                title="Stop this executor"
              >
                <Square className="h-3.5 w-3.5" />
                {stop.stoppingIds.has(activeExec.id) ? "Stopping…" : "Stop"}
              </button>
            )}

            {liveCtrl && (
              <button
                onClick={() => toggleMutation.mutate()}
                disabled={toggleMutation.isPending || isStopping}
                className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
                  isStopping
                    ? "text-[var(--color-yellow)]"
                    : isKilled
                      ? "text-[var(--color-green)] hover:bg-[var(--color-green)]/10"
                      : "text-[var(--color-yellow)] hover:bg-[var(--color-yellow)]/10"
                }`}
                title={isStopping ? "Stopping..." : isKilled ? "Start controller" : "Pause controller"}
              >
                {toggleMutation.isPending || isStopping ? (
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                ) : isKilled ? (
                  <>
                    <Play className="h-3.5 w-3.5" />
                    Start
                  </>
                ) : (
                  <>
                    <Pause className="h-3.5 w-3.5" />
                    Pause
                  </>
                )}
              </button>
            )}
            {/* The config editor: a drawer you open, not a column that is always
                there. It only describes a single controller, so an aggregate
                scope has nothing for it to show. */}
            {liveCtrl && (
              <button
                onClick={() => setDrawer((d) => (d === "config" ? null : "config"))}
                className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                  openDrawer === "config"
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                }`}
                title={openDrawer === "config" ? "Hide config" : "Show config"}
              >
                <SlidersHorizontal className="h-3.5 w-3.5" />
                Config
              </button>
            )}

            {/* Controller sources and their configs, over the whole fleet — so
                it belongs to every scope rather than to one. */}
            <button
              onClick={openEditor}
              className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Open the controller & config editor"
            >
              <TerminalSquare className="h-3.5 w-3.5" />
              Editor
            </button>
          </div>
        </div>

        {/* Body: report pane (+ config drawer) */}
        <div className="flex flex-1 min-h-0">
          {/* The centre column. It scrolls only as a safety valve: the strip,
              the chart's floor and an open band all fit a normal window, and on
              a short one the reader can reach the rows that no longer do rather
              than have them hang off the bottom of the screen. */}
          <div className="flex flex-1 flex-col min-w-0 min-h-0 gap-3 overflow-y-auto scrollbar-thin p-4">
            {/* Headline numbers first: the chart below is the shape of these. */}
            <div className="shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3">
              {/* A fixed set of tiles, not a set that depends on what the scope
                  happens to have. Two reasons. The strip's height decides the
                  chart's, so tiles coming and going would resize the chart as
                  the reader walks the sidebar. And a tile reading "—" is a
                  fact worth having: a terminated scope holds no unrealized PnL,
                  and a live one has not won or lost anything yet — which is
                  different from those numbers being unavailable. */}
              {/* Reflowed by the *pane's* width, not the window's. `lg:` is a
                  viewport breakpoint, so on a wide screen these stayed eight
                  across however narrow the pane got — and opening the executor
                  detail column takes ~480px off it, at which point eight
                  columns of a hundred-odd pixels each printed
                  "REALIZEDUNREALIZED" over one another. `auto-fit` asks the
                  column the tiles are actually in. */}
              <div className="grid grid-cols-[repeat(auto-fit,minmax(104px,1fr))] gap-4">
                <Kpi
                  label="Net PnL"
                  value={formatCurrencyPnl(totals.net, currencySymbol)}
                  color={pnlColor(totals.net)}
                  // The return % is per-leaf — each is measured against its own
                  // notional — so it rides along only where it means something.
                  // Summing or averaging them across a fold would report a
                  // return nobody earned.
                  sub={[
                    perHour(totals.net, formatCurrencyPnl),
                    totals.returnPct !== undefined
                      ? `${totals.returnPct >= 0 ? "+" : ""}${totals.returnPct.toFixed(2)}%`
                      : undefined,
                  ]
                    .filter(Boolean)
                    .join(" · ") || undefined}
                />
                <Kpi
                  label="Realized"
                  value={formatCurrencyPnl(totals.realized, currencySymbol)}
                  color={pnlColor(totals.realized)}
                  sub={perHour(totals.realized, formatCurrencyPnl)}
                />
                <Kpi
                  label="Unrealized"
                  value={formatCurrencyPnl(totals.unrealized, currencySymbol)}
                  color={pnlColor(totals.unrealized)}
                  // What the unrealized figure is *of*: with no open position
                  // there is nothing for it to be, and a stale non-zero reading
                  // beside "no open positions" is worth seeing.
                  sub={
                    totals.positions === 0
                      ? "no open positions"
                      : `${totals.positions} position${totals.positions !== 1 ? "s" : ""}`
                  }
                />
                {/* Win rate is only meaningful over what has *closed*, so the
                    count beside it is the denominator rather than decoration:
                    a fold of live controllers has closed nothing and says so,
                    and a mixed fold quotes the closed subset it was measured
                    over rather than pretending the open ones lost. */}
                <Kpi
                  label="Win rate"
                  value={totals.winRate === undefined ? "—" : `${(totals.winRate * 100).toFixed(1)}%`}
                  sub={
                    totals.closed > 0
                      ? `${totals.wins.toLocaleString()} of ${totals.closed.toLocaleString()} closed`
                      : "nothing closed yet"
                  }
                />
                <Kpi
                  label="Volume"
                  value={formatCurrencyVolume(totals.volume, currencySymbol)}
                  sub={perHour(totals.volume, formatCurrencyVolume)}
                />
                <Kpi
                  label="Fees"
                  value={totals.fees > 0 ? formatCurrencyVolume(totals.fees, currencySymbol) : "—"}
                  // What the fees ate: the share of gross the venue took, which
                  // is the thing an absolute fee total cannot say on its own.
                  sub={
                    totals.fees > 0 && totals.net + totals.fees !== 0
                      ? `${((totals.fees / Math.abs(totals.net + totals.fees)) * 100).toFixed(1)}% of gross`
                      : undefined
                  }
                />
                <Kpi
                  label="Capital"
                  value={totals.capital > 0 ? formatCurrencyVolume(totals.capital, currencySymbol) : "—"}
                  // Turnover rather than a controller count: how hard the
                  // capital is working is the thing the two numbers beside it
                  // do not already say, and the count is on the Runtime tile.
                  sub={
                    totals.capital > 0 ? `${(totals.volume / totals.capital).toFixed(1)}x turnover` : undefined
                  }
                />
                <Kpi
                  label="Runtime"
                  value={totals.hours > 0 ? formatRuntimeHours(totals.hours) : "—"}
                  // Named in hours, not "2d 9h", because it is the divisor of
                  // every per-hour figure in this row — the reader can check the
                  // pace against the total without converting anything. Under
                  // the hour it is minutes: `0.1h` is not a number anyone can
                  // check a pace against (see `formatRuntimeHours`).
                  sub={
                    activeCtrl
                      ? activeCtrl.bot_name
                      : `${plural(totals.bots, "bot")}, ${plural(totals.count, scopeNoun)}`
                  }
                />
              </div>

              {/* ── How the positions ended (FEAT-085) ──

                  Inside the same card as the tiles, because it is the same kind
                  of fact at a different cardinality: the tiles are what this
                  scope adds up to, and these are what it adds up to broken out
                  by close type. They cannot *be* tiles — the fixed six-column
                  grid above breaks the moment a fleet shows seven distinct
                  types — and they were not worth the third of the pane the
                  bottom band used to spend on three short chips.

                  One row that scrolls sideways rather than wrapping, and that
                  is load-bearing: the chart is sized from what this strip
                  leaves, so a strip whose height depended on how many close
                  types a scope happens to have would resize the chart as the
                  reader walked the sidebar. A scope with none drops the row
                  entirely — a fleet of LP controllers should not pay for a line
                  that says nothing. */}
              {totals.closeTypes.length > 0 && (
                <div
                  data-close-types
                  className="mt-3 flex items-center gap-1.5 overflow-x-auto scrollbar-thin border-t border-[var(--color-border)]/60 pt-2"
                >
                  <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                    Closes{" "}
                    <span className="font-semibold tabular-nums text-[var(--color-text)]">
                      {totals.closeTotal.toLocaleString()}
                    </span>
                  </span>
                  {totals.closeTypes.map(([type, count]) => (
                    <span
                      key={type}
                      className="inline-flex shrink-0 items-center gap-1 rounded-md bg-[var(--color-bg)] px-2 py-0.5 text-[11px] border border-[var(--color-border)]/50"
                    >
                      <span className="text-[var(--color-text-muted)]">{parseSide(type)}</span>
                      <span className="font-semibold tabular-nums">{count}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* The chart takes whatever vertical room is left, down to the room
                it actually occupies and no further: the panes are drawn at
                `MIN_CHART_PX` however little is left, so a box shorter than that
                plus its chrome does not shrink the chart — it makes the chart
                overflow downward and paint across the band's header, which is
                what swallowed the "Positions held" label when a twelve-row fleet
                opened the band. The floor is the same two constants the panes are
                sized from, so the two can't drift apart. `overflow-hidden` is the
                belt to that braces: if a pane ever is squeezed below its floor,
                it clips itself instead of covering its neighbour. */}
            <div
              ref={chartRef}
              className="relative flex-1 overflow-hidden"
              style={{ minHeight: MIN_CHART_PX + CHART_CHROME_PX }}
            >
              <div className="absolute inset-0">
                {activeExec && execHasPrice && execChart === "price" ? (
                  /* One executor selected, drawn the way the executors page
                     drew it: the pair's candles with this executor's entry,
                     exit, barriers and — for a grid — its levels laid over
                     them. The aggregate panes below are right for a fold of
                     many records and say almost nothing about a single one,
                     whose whole story is where it opened and where it closed.

                     Keyed by executor id so switching between two of them
                     remounts rather than re-pointing a chart that keeps the
                     previous one's visible range. */
                  <ExecutorChart
                    key={activeExec.id}
                    server={server}
                    executors={execChartGroup}
                    connector={activeExec.connector}
                    tradingPair={activeExec.trading_pair}
                    height={execChartHeight}
                  />
                ) : activeCtrl && population === "running" ? (
                  <ControllerPnlChart
                    // Keyed by bot + config id: two bots can be running this very
                    // config, and a key that did not tell them apart would keep the
                    // sibling's mounted state when the user switches (CORR-241).
                    key={controllerKey(activeCtrl)}
                    server={server}
                    controllerId={configId}
                    botName={activeCtrl.bot_name}
                    deployedAt={activeCtrl.deployed_at}
                    height={chartHeight}
                    convert={convert}
                    currencySymbol={currencySymbol}
                    controller={activeCtrl}
                  />
                ) : chartData.length >= 2 ? (
                  <PnlEvolutionChart
                    data={chartData}
                    title={chartTitle}
                    pnlHeight={Math.round(chartHeight * 0.65)}
                    volumeHeight={chartHeight - Math.round(chartHeight * 0.65)}
                    currencySymbol={currencySymbol}
                    notice={notice}
                  />
                ) : runHistoryLoading ? (
                  // The first open of a cold run pays one walk. Behind the same
                  // spinner a cold archive already shows, and never blocking the
                  // tree: the sidebar and the strip are drawn from the
                  // controllers listing whether or not this has landed.
                  <div className="flex h-full items-center justify-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
                    <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />
                    <p className="text-xs text-[var(--color-text-muted)]">Reading this run's history…</p>
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-6">
                    <p className="max-w-sm text-center text-xs text-[var(--color-text-muted)]">
                      {archiveOnlyController
                        ? "This run predates the server's stored performance history. Its archived database records the run as a whole rather than per controller — select the bot above to see that curve."
                        : "No performance history available"}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* ── The bottom band (FEAT-085, FEAT-086) ──

                A one-line header pinned under the chart, opening over it when
                the reader wants a breakdown. Two things can be underneath a
                scope — the positions it is holding and the executors that make
                it up — and they share one band rather than stacking, so the
                chart's height depends on the strip alone and not on what the
                selected node happens to have.

                Both are tables rather than grids of cards: at fleet scope these
                are dozens or thousands of one-line facts, and a row each is
                what makes them comparable at a glance.

                Open, it asks for up to 45% of the pane but yields to the chart's
                floor above — the chart is the reason the reader is here, and a
                table that scrolls two rows shorter costs less than a curve drawn
                in half the room it needs. */}
            {(positionRows.length > 0 || scopedExecutors.length > 0) && (
              <div
                className={`flex min-h-0 flex-col rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden ${
                  band ? "max-h-[45%] min-h-[132px]" : "shrink-0"
                }`}
              >
                <div className="flex shrink-0 items-center">
                  {positionRows.length > 0 && (
                    <BandTab
                      label={`Positions held (${positionRows.length})`}
                      open={band === "positions"}
                      onClick={() => toggleBand("positions")}
                      data-positions-toggle
                    />
                  )}
                  {scopedExecutors.length > 0 && (
                    <BandTab
                      label={`Executors (${scopedExecutors.length})`}
                      open={band === "executors"}
                      onClick={() => toggleBand("executors")}
                      data-executors-toggle
                    />
                  )}
                </div>

                {band === "executors" && scopedExecutors.length > 0 && (
                  <div className="min-h-0 flex-1 border-t border-[var(--color-border)]/60">
                    <ExecutorRows
                      executors={scopedExecutors}
                      stop={stop}
                      selectedId={detail?.id ?? null}
                      onSelect={setDetail}
                      rateFormatPnl={rateFormatPnl}
                      rateFormatValue={rateFormatValue}
                      rateFormatDetailed={rateFormatDetailed}
                    />
                  </div>
                )}

                {band === "positions" && positionRows.length > 0 && (
                  <PositionsTable
                    rows={positionRows}
                    currencySymbol={currencySymbol}
                    // Every row would repeat the controller's name when the
                    // scope already is one controller.
                    showController={!activeCtrl}
                  />
                )}
              </div>
            )}
          </div>

          {/* The right drawer: one column, whichever of the two the scope
              supports. A controller's config, or a bot's logs — never both,
              and never one belonging to a scope the user has left. */}
          {openDrawer && (
            <div className="w-[380px] xl:w-[440px] shrink-0 border-l border-[var(--color-border)] flex flex-col bg-[var(--color-surface)]">
              {openDrawer === "config" && activeCtrl ? (
                <YamlConfigEditor
                  // Same: an editor keyed on the config id alone kept its unsaved
                  // buffer when the user switched to the other bot running it.
                  key={controllerKey(activeCtrl)}
                  config={activeCtrl.config || {}}
                  server={server}
                  configId={configId}
                  botName={activeCtrl.bot_name}
                  onSaved={() => queryClient.invalidateQueries({ queryKey: ["bots", server] })}
                  onCollapse={() => setDrawer(null)}
                />
              ) : (
                <div className="flex h-full min-h-0 flex-col">
                  <div className="flex shrink-0 items-center gap-2 border-b border-[var(--color-border)]/50 px-4 py-2">
                    <button
                      onClick={() => setDrawer(null)}
                      className="rounded p-0.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                      title="Hide logs"
                    >
                      <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                    <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                      Logs
                    </h3>
                  </div>
                  <div className="flex-1 min-h-0 p-3">
                    <LogsSection logs={botLogs} />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      )}

      {/* The executor detail panel, in the same slot the config and logs
          drawers use: a scope-owned column, closed until a row is clicked. */}
      {detail && (
        <DetailPanel
          executor={detail}
          server={server}
          onClose={() => setDetail(null)}
          onStop={(id) => stop.request([id])}
          stopping={stop.stoppingIds.has(detail.id)}
          rateFormatPnl={rateFormatPnl}
          rateFormatValue={rateFormatValue}
          rateFormatDetailed={rateFormatDetailed}
        />
      )}

      {stop.pendingIds && (
        <StopConfirmDialog
          ids={stop.pendingIds}
          executors={scopedExecutors}
          onConfirm={stop.confirm}
          onCancel={stop.cancel}
        />
      )}

      <DeployBotDialog open={showDeploy} onClose={() => setShowDeploy(false)} server={server} />

      {/* Kept mounted once opened, hidden rather than unmounted while closed:
          the editor holds unsaved buffers across tabs, and closing the modal is
          not the same as discarding them — which is what an unmount would do,
          silently. */}
      {editorMounted && (
        <EditorModal open={editorOpen} onClose={() => setEditorOpen(false)} />
      )}
    </div>
  );
}
