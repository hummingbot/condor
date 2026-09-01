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
  RotateCcw,
  Save,
  ScrollText,
  Server,
  SlidersHorizontal,
  Square,
  TerminalSquare,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "react-router-dom";
import yamlLib from "js-yaml";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { EditorModal } from "@/components/editor/EditorModal";
import { ControllerPnlChart } from "@/components/bots/ControllerPnlChart";
import { DeployBotDialog } from "@/components/bots/DeployBotDialog";
import { LogsSection } from "@/components/bots/LogsSection";
import { PnlEvolutionChart } from "@/components/bots/PnlEvolutionChart";
import { DetailPanel } from "@/components/perf/ExecutorTable";
import { ExecutorRows, StopConfirmDialog } from "@/components/perf/ExecutorRows";
import { useExecutorStop } from "@/components/perf/executorActions";
import { BubbleGroup, type BubbleOption } from "@/components/perf/FilterBubbles";
import { PopulationToggle } from "@/components/perf/PopulationToggle";
import { ScopeTree, StatusDot } from "@/components/perf/ScopeTree";
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
import { configToYaml, CONTROLLER_HIDDEN_KEYS } from "@/lib/configYaml";
import {
  formatCurrencyVolume,
  formatCurrencyPnl,
  isExecutorActive,
  pnlColor,
  shortBotName,
  toMs,
} from "@/lib/formatters";
import {
  buildTree,
  collectLeaves,
  controllerNodeId,
  foldLeaves,
  indexTree,
  leafFromController,
  leafFromExecutor,
  leafFromTerminatedController,
  parsePopulation,
  resolveScope,
  runStatus,
  UNATTACHED_BOT,
  visibleNodeIds,
  type Population,
  type PerfLeaf,
  type PerfNode,
} from "@/lib/perf-tree";
import { aggregatePnlSeries, executorSeries, snapshotsFromRunHistory } from "@/lib/pnl-chart";
import { buildAttributor, runWindows } from "@/lib/run-attribution";
import type { ConvertFn } from "@/lib/rates";
import { useViewFacts } from "@/lib/viewFacts";

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

function parseSide(raw: string): string {
  const dot = raw.lastIndexOf(".");
  return dot >= 0 ? raw.slice(dot + 1) : raw;
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
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
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

// ── YAML Config Editor ──

function YamlConfigEditor({
  config,
  server,
  configId,
  botName,
  onSaved,
  onCollapse,
}: {
  config: Record<string, unknown>;
  server: string;
  configId: string;
  botName: string;
  onSaved: () => void;
  onCollapse: () => void;
}) {
  // Memoize the dump so typing / local-state renders don't re-run yaml.dump, and
  // so WS-tick churn of the `config` object identity that yields the SAME content
  // produces an identical string (compared by value) below.
  const originalYaml = useMemo(
    () =>
      configToYaml(config, {
        hiddenKeys: CONTROLLER_HIDDEN_KEYS,
        stripUnderscore: true,
        sortKeys: true,
      }),
    [config],
  );
  const [yamlContent, setYamlContent] = useState(originalYaml);
  const [parseError, setParseError] = useState<string | null>(null);

  // Sync when config content actually changes (save / controller switch). Keyed
  // on the string value, not the `config` object: a tick that re-creates `config`
  // with unchanged content leaves `originalYaml` equal, so unsaved edits survive.
  useEffect(() => {
    setYamlContent(originalYaml);
    setParseError(null);
  }, [originalYaml]);

  const isDirty = yamlContent !== originalYaml;

  const handleChange = useCallback((value: string) => {
    setYamlContent(value);
    try {
      yamlLib.load(value);
      setParseError(null);
    } catch (e) {
      setParseError((e as Error).message?.split("\n")[0] || "Invalid YAML");
    }
  }, []);

  const saveMutation = useMutation({
    mutationFn: () => {
      const parsed = yamlLib.load(yamlContent) as Record<string, unknown>;
      if (!parsed || typeof parsed !== "object") {
        throw new Error("YAML must be a mapping");
      }
      return api.updateBotControllerConfig(server, botName, configId, parsed);
    },
    onSuccess: () => {
      onSaved();
    },
  });

  return (
    <div className="flex flex-col h-full">
      {/* Header with save */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--color-border)]/50">
        <div className="flex items-center gap-2 min-w-0">
          <button
            onClick={onCollapse}
            className="rounded p-0.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title="Hide config"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Config
          </h3>
          {isDirty && (
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-yellow)]" title="Unsaved changes" />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {isDirty && (
            <button
              onClick={() => { setYamlContent(originalYaml); setParseError(null); }}
              className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            >
              <RotateCcw className="h-3 w-3" />
              Reset
            </button>
          )}
          <button
            onClick={() => saveMutation.mutate()}
            disabled={!isDirty || !!parseError || saveMutation.isPending}
            className="flex items-center gap-1 rounded px-2.5 py-1 text-[10px] font-semibold transition-colors disabled:opacity-30 bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary)]/80 disabled:hover:bg-[var(--color-primary)]"
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Save className="h-3 w-3" />
            )}
            Save
          </button>
        </div>
      </div>

      {parseError && (
        <div className="px-4 py-1.5 text-[10px] text-[var(--color-red)] bg-[var(--color-red)]/5 truncate" title={parseError}>
          {parseError}
        </div>
      )}
      {saveMutation.isError && (
        <div className="px-4 py-1.5 text-[10px] text-[var(--color-red)] bg-[var(--color-red)]/5">
          {(saveMutation.error as Error).message}
        </div>
      )}
      {saveMutation.isSuccess && !isDirty && (
        <div className="px-4 py-1.5 text-[10px] text-[var(--color-green)] bg-[var(--color-green)]/5">
          Config saved successfully
        </div>
      )}

      {/* YAML editor */}
      <div className="flex-1 min-h-0">
        <CodeEditor
          value={yamlContent}
          onChange={handleChange}
          language="yaml"
          height="100%"
          className="border-0 rounded-none"
        />
      </div>
    </div>
  );
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

// ── Positions table ──

/** The keys the table has its own column for; anything else is an extra. */
const POSITION_PRIMARY_KEYS = new Set([
  "connector_name",
  "connector",
  "trading_pair",
  "side",
  "realized_pnl_quote",
  "unrealized_pnl_quote",
  "volume_traded_quote",
  "volume_traded",
  "amount",
  "breakeven_price",
]);

interface PositionRow {
  id: string;
  ctrlLabel: string;
  pair: string;
  connector: string;
  side: string;
  amount: number | null;
  breakeven: number | null;
  /** Amount x breakeven, in display currency: what the inventory is worth.
      Unsigned — the Side column already says which way it points. */
  notional: number | null;
  realized: number;
  unrealized: number;
  volume: number;
  extras: [string, unknown][];
}

function num(v: unknown): number | null {
  const n = Number(v);
  return v === undefined || v === null || Number.isNaN(n) ? null : n;
}

/** Prices and amounts vary over many orders of magnitude; trim rather than pad. */
function formatAmount(v: number | null): string {
  if (v === null) return "—";
  if (v === 0) return "0";
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 8;
  return v.toFixed(digits).replace(/\.?0+$/, "");
}

/**
 * The quote value of an open position, in display currency.
 *
 * `positions_summary` carries no mark price, so the breakeven is the price we
 * have — this is the cost basis of the inventory, not its mark-to-market
 * value; the two differ by exactly the Unrealized column beside it. Same
 * convention as `positionQuoteValue` in lib/pnl-chart, which is what the
 * chart's position series is built from, so the two agree.
 */
function positionNotional(
  pos: Record<string, unknown>,
  pair: string,
  cv: (val: number, pair: string) => number,
): number | null {
  const amount = num(pos.amount);
  const price = num(pos.breakeven_price);
  if (amount === null || price === null) return null;
  return cv(Math.abs(amount * price), pair);
}

function SideTag({ side }: { side: string }) {
  if (!side) return <span className="text-[var(--color-text-muted)]">—</span>;
  const buy = side.toLowerCase() === "buy";
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase"
      style={{
        color: buy ? "var(--color-green)" : "var(--color-red)",
        background: buy ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
      }}
    >
      {side}
    </span>
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
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
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
  const [open, setOpen] = useState<Set<string>>(() => new Set([FLEET_SCOPE]));
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
   * These are the filters the executors page carried, split into the three
   * questions a reader actually asks — *whose*, *what class of controller*,
   * *what kind of executor* — and they narrow the whole tree rather than one
   * table under a total that ignored them. The bot and class of a record used
   * to be a *level* of the tree instead, chosen by a `By bot / By type` toggle;
   * as filters they combine (several bots at once, one class across all of
   * them) and they cost the reader no chevron to walk through.
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
    bots: [] as string[],
    ctrlTypes: [] as string[],
    execTypes: [] as string[],
  });
  // How far back a terminated fold reaches. It exists for the terminated
  // population alone: a live controller's runtime is its deploy, not a window
  // the reader picks.
  const [period, setPeriod] = useState<PeriodKey>("3M");

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

  const now = useSyncExternalStore(subscribeToClock, clockSnapshot, clockSnapshot);

  // The scope lives in the URL, so `?scope=ctrl:<bot>:<config id>` is a link to
  // one controller and a reload lands back on it. Written with `replace` — the
  // arrow keys walk the sidebar, and every step of that walk in the history
  // stack would make Back useless.
  const [searchParams, setSearchParams] = useSearchParams();
  const scopeId = searchParams.get("scope") || FLEET_SCOPE;
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
    (next: string) => setParam("scope", next, FLEET_SCOPE),
    [setParam],
  );
  // `switchView` is declared after the tree it needs; both setters below it.

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
        for (const c of controllers) all.push(leafFromController(c));
        for (const ex of executors) {
          if (isExecutorActive(ex.status)) all.push(leafFromExecutor(ex, botOf(ex)));
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
          const leaf = leafFromExecutor(ex, closedBotOf(ex, started));
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
          const leaf = leafFromTerminatedController(ctrl, runByBot.get(ctrl.bot_name));
          if (cutoff && leaf.endedAt !== null && leaf.endedAt < cutoff) continue;
          all.push(leaf);
        }
      }

      return all;
    },
    [controllers, executors, runByBot, terminatedControllers, botByController, attribute, period, now],
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
   */
  const applyFilters = useCallback(
    (all: PerfLeaf[]): PerfLeaf[] => {
      const pair = filters.pair.trim().toLowerCase();
      const { bots: wantBots, ctrlTypes, execTypes } = filters;
      if (!pair && !wantBots.length && !ctrlTypes.length && !execTypes.length) return all;
      return all.filter((leaf) => {
        if (pair && !leaf.pair.toLowerCase().includes(pair)) return false;
        if (wantBots.length && !wantBots.includes(leaf.bot)) return false;
        if (ctrlTypes.length) {
          const cls =
            leaf.kind === "controller"
              ? leaf.executorType
              : ctrlClassById.get(leaf.controllerId) ?? "";
          if (!ctrlTypes.includes(cls)) return false;
        }
        if (execTypes.length) {
          if (leaf.kind === "controller") return false;
          if (!execTypes.includes(leaf.executorType)) return false;
        }
        return true;
      });
    },
    [filters, ctrlClassById],
  );

  /** The population as it stands, and what the bubbles left of it. */
  const rawLeaves = useMemo(() => leavesFor(population), [leavesFor, population]);
  const leaves = useMemo(() => applyFilters(rawLeaves), [applyFilters, rawLeaves]);

  /**
   * What the three bubble groups offer, and how big each bucket is.
   *
   * Derived from the population *before* the filters are applied, so ticking a
   * bubble never removes the other bubbles from the row it was ticked in — a
   * filter that eats its own options cannot be undone without clearing it — and
   * a count never renumbers itself as a consequence of being ticked.
   *
   * Bots are ordered by what started most recently, which on the terminated
   * side is the order a reader arrives looking for: the run that just finished
   * is the one they came about. Classes and types are alphabetical, being
   * vocabularies rather than events.
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

    const latestStart = new Map<string, number>();
    for (const leaf of rawLeaves) {
      const at = leaf.startedAt ?? 0;
      if (at > (latestStart.get(leaf.bot) ?? -1)) latestStart.set(leaf.bot, at);
    }
    const botCounts = tally((leaf) => leaf.bot, rawLeaves);
    const bots: BubbleOption[] = [...botCounts]
      .map(([value, count]) => ({ value, label: shortBotName(value), count }))
      .sort((a, b) => (latestStart.get(b.value) ?? 0) - (latestStart.get(a.value) ?? 0));

    const alpha = (counts: Map<string, number>): BubbleOption[] =>
      [...counts]
        .map(([value, count]) => ({ value, label: value, count }))
        .sort((a, b) => a.label.localeCompare(b.label));

    return {
      bots,
      // A controller's own class, counted over controllers — an executor
      // inherits its controller's class rather than carrying one, so counting
      // it here would report the same controller once per executor under it.
      ctrlTypes: alpha(
        tally(
          (leaf) => (leaf.kind === "controller" ? leaf.executorType : ""),
          rawLeaves,
        ),
      ),
      execTypes: alpha(
        tally((leaf) => (leaf.kind === "executor" ? leaf.executorType : ""), rawLeaves),
      ),
    };
  }, [rawLeaves]);

  const filtersActive =
    !!filters.pair.trim() ||
    filters.bots.length > 0 ||
    filters.ctrlTypes.length > 0 ||
    filters.execTypes.length > 0;

  /**
   * The one bot every row on screen belongs to, when there is one.
   *
   * A bot used to be a node you could select, and selecting it is what put its
   * name in the header and its actions — stop, logs, open archive, delete run —
   * beside them. With the bot level retired, narrowing the bubbles to a single
   * bot *is* that selection: the fleet row folds exactly that bot's records, so
   * it is that bot's report and gets that bot's buttons.
   *
   * Read off the population rather than off the filter, so a bot that is alone
   * on the server needs no bubble ticked to be recognised as itself.
   */
  const soloBot = useMemo(() => {
    const seen = new Set(leaves.map((leaf) => leaf.bot));
    return seen.size === 1 ? [...seen][0] : undefined;
  }, [leaves]);
  const soloRealBot = soloBot && soloBot !== UNATTACHED_BOT ? soloBot : undefined;

  /** What the fleet row is called, which depends on what is under it. */
  const rootLabel = useCallback(
    (which: Population, bot?: string) =>
      bot ? shortBotName(bot) : which === "running" ? "All controllers" : "All closed",
    [],
  );

  const tree = useMemo(
    () => buildTree(leaves, rootLabel(population, soloRealBot)),
    [leaves, population, soloRealBot, rootLabel],
  );
  const nodes = useMemo(() => indexTree(tree), [tree]);


  // A scope whose node has gone — a bot stopped, a config removed — would
  // render an empty screen with no way back, so it re-aims at the nearest
  // ancestor that survived rather than resetting to the fleet (see
  // `resolveScope`, which reads that ancestry out of the id itself).
  const effectiveScopeId = useMemo(() => resolveScope(nodes, scopeId), [nodes, scopeId]);
  const scope = useMemo(() => nodes.get(effectiveScopeId) ?? tree, [nodes, effectiveScopeId, tree]);

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
      const nextTree = buildTree(
        applyFilters(leavesFor(nextPopulation)),
        rootLabel(nextPopulation),
      );
      const aimed = resolveScope(indexTree(nextTree), effectiveScopeId, scope.leaves[0]);
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev);
          if (nextPopulation === "running") params.delete("population");
          else params.set("population", nextPopulation);
          if (aimed === FLEET_SCOPE) params.delete("scope");
          else params.set("scope", aimed);
          return params;
        },
        { replace: true },
      );
    },
    [population, leavesFor, applyFilters, rootLabel, effectiveScopeId, scope, setSearchParams],
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
   * The run the terminated fleet row is reporting on.
   *
   * A finished run has no node of its own — the `runs` branch it used to hang
   * under went in FEAT-089, and the bot node that replaced it went with the
   * grouping level — so the two actions that belong to a run, opening its
   * archive and deleting it, belong to the fleet row *when that row is exactly
   * one run*. Undefined for `(unattached)`, which is not a run and has neither.
   */
  const activeRun =
    population === "terminated" && scope.kind === "fleet" && soloRealBot
      ? runByBot.get(soloRealBot)
      : undefined;

  /** The executors under this scope, whatever level it sits at. */
  const scopedExecutors = useMemo(
    () => collectLeaves(scope, "executor").map((leaf) => leaf.source as ExecutorInfo),
    [scope],
  );

  /**
   * The controller an executor scope hangs under, if any.
   *
   * An executor has no series of its own — `controller_performance_snapshots`
   * samples controllers, and an executor is one mutable row updated in place —
   * so the chart for one is its parent's, said in those words rather than
   * passed off as the executor's own curve.
   */
  const parentController = useMemo((): PerfNode | undefined => {
    if (scope.kind !== "executor") return undefined;
    const parentId = controllerNodeId(scope.leaves[0]);
    return parentId ? nodes.get(parentId) : undefined;
  }, [scope, nodes]);

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

  const activeBot =
    scope.kind === "fleet" && soloRealBot
      ? bots.find((b) => b.bot_name === soloRealBot)
      : undefined;
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

  // Deleting a finished run: irreversible, and the only door to it now that the
  // runs table is gone, so it keeps the table's arm-then-confirm.
  const deleteRunMutation = useMutation({
    mutationFn: (botRunId: number) => api.deleteBotRun(server, botRunId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bot-runs", server] });
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

  /** The controller the selected row hangs under, when the selection is an executor. */
  const selectedParent = useMemo(() => {
    const node = nodes.get(effectiveScopeId);
    return node?.kind === "executor" ? controllerNodeId(node.leaves[0]) : null;
  }, [nodes, effectiveScopeId]);

  /**
   * What is actually drawn open, which is what the reader opened *plus* the
   * branch holding the selection.
   *
   * A controller starts shut, so a `?scope=exec:…` link — or a scope that
   * survived a filter change by falling back to its controller — would land on
   * a row inside a branch nobody had opened: highlighted in the panes, invisible
   * in the picker, and unreachable by the arrow keys, which walk what is drawn.
   * Derived rather than written back into `open` from an effect, so the state
   * stays the reader's own record of what they opened.
   */
  const openRows = useMemo(
    () => (selectedParent && !open.has(selectedParent) ? new Set(open).add(selectedParent) : open),
    [open, selectedParent],
  );

  const toggleOpen = useCallback(
    (id: string) => {
      setOpen((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      // Shutting the branch that holds the selection takes the selection up
      // with it. Without this the row would be hidden and re-opened in the same
      // click by `openRows`, and the chevron would read as broken.
      if (selectedParent === id && openRows.has(id)) setScope(id);
    },
    [selectedParent, openRows, setScope],
  );

  const navItems = useMemo(() => visibleNodeIds(tree, openRows), [tree, openRows]);
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

  const positionRows = useMemo<PositionRow[]>(() => {
    const rows: PositionRow[] = [];
    for (const leaf of scopedLeaves) {
      leaf.positions.forEach((pos, i) => {
        const pair = String(pos.trading_pair || leaf.pair || "");
        rows.push({
          id: `${leaf.id}#${i}`,
          ctrlLabel: leaf.label,
          pair,
          connector: String(pos.connector_name || pos.connector || leaf.connector || ""),
          side: parseSide(String(pos.side || "")),
          amount: num(pos.amount),
          breakeven: num(pos.breakeven_price),
          notional: positionNotional(pos, pair, cv),
          realized: cv(Number(pos.realized_pnl_quote || 0), pair),
          unrealized: cv(Number(pos.unrealized_pnl_quote || 0), pair),
          volume: cv(Number(pos.volume_traded_quote || pos.volume_traded || 0), pair),
          extras: Object.entries(pos).filter(([k]) => !POSITION_PRIMARY_KEYS.has(k)),
        });
      });
    }
    return rows;
  }, [scopedLeaves, cv]);

  /** Extra per-position fields, as columns, so the table stays one grid. */
  const extraColumns = useMemo(() => {
    const seen: string[] = [];
    for (const row of positionRows) {
      for (const [k] of row.extras) if (!seen.includes(k)) seen.push(k);
    }
    return seen;
  }, [positionRows]);

  // ── The aggregated series, folded from the fleet history the page already has ──

  /**
   * The live controllers the chart draws, which is not always the scope's own:
   * an executor node inherits its parent controller's series (see
   * `parentController`).
   */
  const scopedControllers = useMemo(
    () =>
      (parentController ? parentController.leaves : scopedLeaves)
        .filter((leaf: PerfLeaf) => leaf.kind === "controller")
        .map((leaf) => leaf.source as ControllerInfo),
    [parentController, scopedLeaves],
  );

  const scopedKeys = useMemo(
    () => new Set(scopedControllers.map((c) => controllerKey(c))),
    [scopedControllers],
  );

  /**
   * Where this scope's series comes from — the one place the two populations
   * genuinely differ.
   *
   * A live controller, bot or fleet folds the *sampled* history the page
   * already walked. Anything terminated has no sampled history to fold — an
   * executor is one mutable row upstream, not a time series — so its series is
   * built from the outcomes themselves: each close, at its close time, summed
   * (see `executorSeries`). And a live executor has neither, so it borrows its
   * parent controller's curve and the card says whose it is.
   *
   * All three return the same `PnlChartPoint[]`, so the chart is untouched by
   * any of it. [[FEAT-087]] replaces the second and third with real snapshots,
   * and this is the seam it swaps at.
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

  const chartData = useMemo(() => {
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
      return executorSeries(scope.leaves, cv);
    }
    return aggregatePnlSeries(snapshots, scopedKeys, scopedControllers, convert);
  }, [
    activeCtrl, population, scope, cv, snapshots, scopedKeys, scopedControllers,
    convert, runHistory, scopeRun, archiveOnlyController,
  ]);

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
      ? "controller"
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
    /** `bot alpha/beta`, or `bot 7 selected` once a list stops being readable. */
    const picked = (noun: string, values: string[]) =>
      values.length === 0
        ? ""
        : values.length <= 3
          ? `${noun} ${values.join("/")}`
          : `${noun} ${values.length} selected`;
    const chips = [
      filters.pair.trim() ? `pair ~ "${filters.pair.trim()}"` : "",
      picked("bot", filters.bots),
      picked("controller type", filters.ctrlTypes),
      picked("executor type", filters.execTypes),
    ].filter(Boolean);

    const subject = activeCtrl
      ? `controller "${activeCtrl.controller_name}" (${activeCtrl.trading_pair}) of bot ${activeCtrl.bot_name}`
      : activeExec
        ? `executor ${activeExec.id} (${activeExec.type}, ${activeExec.trading_pair})`
        : activeRun
          ? `the finished run of bot ${activeRun.bot_name}`
          : scope.kind === "fleet"
            ? soloRealBot
              ? `${plural(scope.leaves.length, scopeNoun)} of bot ${soloRealBot}`
              : `all ${plural(scope.leaves.length, scopeNoun)} in scope`
            : `${plural(scope.leaves.length, scopeNoun)} under ${scope.kind} "${scope.label}"`;

    return {
      // The same label the route entry uses, so the cache's half of this screen
      // and the reader's half render as one screen rather than two.
      label: "Bots",
      subject,
      onScreen: {
        population,
        scope: effectiveScopeId,
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

  if (controllers.length === 0) return null;

  const configId = activeCtrl ? activeCtrl.controller_id || activeCtrl.controller_name : "";
  const chartHeight = Math.max(MIN_CHART_PX, (chartBoxH || 420) - CHART_CHROME_PX);

  // An executor scope borrows its parent's curve, and the card says so in both
  // places a reader might look: the title names whose series it is, and the
  // notice says why the executor has none of its own.
  const inheritedFrom = population === "running" ? parentController?.label : undefined;
  const chartTitle = inheritedFrom
    ? `${inheritedFrom} PnL`
    : scope.kind === "fleet"
      ? population === "terminated"
        ? "Closed PnL"
        : "Fleet PnL"
      : `${scope.label} PnL`;
  // What the terminated chart is actually drawn from, said rather than assumed.
  //
  // It used to claim "closed outcomes" unconditionally, which was true when
  // that was the only thing it could draw. There are three sources now and the
  // reader has no other way to tell them apart: a run's own sampled history
  // reads exactly like a live one, and a fallback that looked identical while
  // meaning something weaker would be the worst of the three.
  const terminatedNotice =
    population !== "terminated"
      ? undefined
      : runHistory && runHistory.points > 0
        ? runHistory.source === "archive"
          ? {
              label: "from the archived database",
              detail:
                "The server kept no performance snapshots this far back, so this curve is rebuilt from the run's archived trade table. It is trade-exact and has no unrealized series, because a closed trade has nothing left unrealized.",
            }
          : undefined
        : runHistory?.source === "none"
          ? {
              label: "no recorded history",
              detail:
                `The server has no stored history for this run${runHistory.detail ? ` — ${runHistory.detail}` : ""}. Its snapshot table only reaches so far back, and this run started before that. The steps below are what its executors closed, at the times they closed.`,
            }
          : {
              label: "closed outcomes",
              detail:
                "Drawn from each executor's close time and its final PnL, not from sampled history — nothing here is still open, so there is no unrealized series and no position to hold. Each step is what closed in that bucket.",
            };

  const chartNotice =
    population === "terminated"
      ? terminatedNotice
      : inheritedFrom
        ? {
            label: "parent controller's series",
            detail:
              "An executor is stored upstream as a single row updated in place, with no sampled history of its own, so the curve below is the controller it belongs to. The numbers above the chart are the executor's own.",
          }
        : truncated
          ? {
              label: "partial history",
              detail:
                "This fleet has more stored history than one chart may load at once, so the series starts later than the earliest deploy.",
            }
          : undefined;

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
                  drawn only when it has something to choose between: a single
                  bot, or a terminated side whose controllers all report the
                  same (unknown) class, is not a filter. */}
              {filterOptions.bots.length > 1 && (
                <BubbleGroup
                  title="Bots"
                  hint="Which bots' records are in scope. Narrow to exactly one and the row below becomes that bot, with its own actions."
                  options={filterOptions.bots}
                  selected={filters.bots}
                  onChange={(v) => setFilters((f) => ({ ...f, bots: v }))}
                  previewCount={6}
                />
              )}
              {filterOptions.ctrlTypes.length > 1 && (
                <BubbleGroup
                  title="Controller type"
                  hint="The class each controller is: pmm_simple, grid_strike, and so on."
                  options={filterOptions.ctrlTypes}
                  selected={filters.ctrlTypes}
                  onChange={(v) => setFilters((f) => ({ ...f, ctrlTypes: v }))}
                />
              )}
              {filterOptions.execTypes.length > 1 && (
                <BubbleGroup
                  title="Executor type"
                  hint="Picking one reports the executors themselves: a controller record covers every type it ever ran, so it steps aside and each row folds the matching executors instead."
                  options={filterOptions.execTypes}
                  selected={filters.execTypes}
                  onChange={(v) => setFilters((f) => ({ ...f, execTypes: v }))}
                />
              )}
              {filtersActive && (
                <button
                  type="button"
                  onClick={() => setFilters({ pair: "", bots: [], ctrlTypes: [], execTypes: [] })}
                  className="self-start text-[10px] text-[var(--color-text-muted)] underline-offset-2 hover:text-[var(--color-text)] hover:underline"
                >
                  Clear all filters
                </button>
              )}
            </div>
          )}
        </div>

        {/* Scope list: fleet → controller → executor, and nothing else */}
        <div ref={sidebarRef} className="flex-1 overflow-y-auto scrollbar-thin">
          <ScopeTree
            root={tree}
            activeId={effectiveScopeId}
            open={openRows}
            showBot={!soloBot}
            onSelect={setScope}
            onToggleOpen={toggleOpen}
            cv={cv}
            currencySymbol={currencySymbol}
            now={now}
            compact={isCompact}
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
            ) : (
              // Every other scope — the whole fleet, one live bot's share of
              // it, or a controller reconstructed out of closed executors. One
              // header for all of them: what it is, and what it folds.
              <div className="truncate">
                <h2 className="text-sm font-semibold truncate flex items-center gap-2">
                  {soloRealBot && scope.kind === "fleet" ? (
                    <Server className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                  ) : (
                    <Layers className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                  )}
                  <span className="truncate" title={soloRealBot ?? scope.label}>
                    {scope.kind !== "fleet"
                      ? scope.label
                      : soloRealBot
                        ? shortBotName(soloRealBot)
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
              <div className="grid grid-cols-4 lg:grid-cols-8 gap-4">
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
                  value={totals.hours > 0 ? `${totals.hours.toFixed(1)}h` : "—"}
                  // Named in hours, not "2d 9h", because it is the divisor of
                  // every per-hour figure in this row — the reader can check the
                  // pace against the total without converting anything.
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
                {activeCtrl && population === "running" ? (
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
                    notice={chartNotice}
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
                  <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin border-t border-[var(--color-border)]/60">
                    <div className="overflow-x-auto">
                      <table className="w-full text-[11px]">
                        <thead>
                          <tr className="border-y border-[var(--color-border)]/60 bg-[var(--color-bg)] text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                            {!activeCtrl && <th className="px-3 py-1.5 text-left font-medium">Controller</th>}
                            <th className="px-3 py-1.5 text-left font-medium">Connector</th>
                            <th className="px-3 py-1.5 text-left font-medium">Pair</th>
                            <th className="px-3 py-1.5 text-left font-medium">Side</th>
                            <th className="px-3 py-1.5 text-right font-medium">Amount</th>
                            <th className="px-3 py-1.5 text-right font-medium">Breakeven</th>
                            <th className="px-3 py-1.5 text-right font-medium" title="Amount x breakeven price">
                              Notional
                            </th>
                            {extraColumns.map((k) => (
                              <th key={k} className="px-3 py-1.5 text-right font-medium">{k}</th>
                            ))}
                            <th className="px-3 py-1.5 text-right font-medium">Realized</th>
                            <th className="px-3 py-1.5 text-right font-medium">Unrealized</th>
                            <th className="px-3 py-1.5 text-right font-medium">Volume</th>
                          </tr>
                        </thead>
                        <tbody>
                          {positionRows.map((row) => {
                            const extras = Object.fromEntries(row.extras);
                            return (
                              <tr key={row.id} className="border-b border-[var(--color-border)]/30 last:border-0">
                                {!activeCtrl && (
                                  <td className="px-3 py-1.5 max-w-[220px] truncate" title={row.ctrlLabel}>
                                    {row.ctrlLabel}
                                  </td>
                                )}
                                <td className="px-3 py-1.5 text-[var(--color-text-muted)]">{row.connector || "—"}</td>
                                <td className="px-3 py-1.5 font-medium">{row.pair || "—"}</td>
                                <td className="px-3 py-1.5"><SideTag side={row.side} /></td>
                                <td className="px-3 py-1.5 text-right tabular-nums">{formatAmount(row.amount)}</td>
                                <td className="px-3 py-1.5 text-right tabular-nums">{formatAmount(row.breakeven)}</td>
                                <td className="px-3 py-1.5 text-right tabular-nums">
                                  {row.notional === null
                                    ? "—"
                                    : formatCurrencyVolume(row.notional, currencySymbol)}
                                </td>
                                {extraColumns.map((k) => {
                                  const val = extras[k];
                                  return (
                                    <td key={k} className="px-3 py-1.5 text-right tabular-nums text-[var(--color-text-muted)]">
                                      {val === undefined || val === null
                                        ? "—"
                                        : typeof val === "number"
                                          ? formatAmount(val)
                                          : parseSide(String(val))}
                                    </td>
                                  );
                                })}
                                <td className="px-3 py-1.5 text-right tabular-nums font-medium" style={{ color: pnlColor(row.realized) }}>
                                  {formatCurrencyPnl(row.realized, currencySymbol)}
                                </td>
                                <td className="px-3 py-1.5 text-right tabular-nums font-medium" style={{ color: pnlColor(row.unrealized) }}>
                                  {formatCurrencyPnl(row.unrealized, currencySymbol)}
                                </td>
                                <td className="px-3 py-1.5 text-right tabular-nums">
                                  {formatCurrencyVolume(row.volume, currencySymbol)}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
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
