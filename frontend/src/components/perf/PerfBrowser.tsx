import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  History,
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
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Link, useSearchParams } from "react-router-dom";
import yamlLib from "js-yaml";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { EditorModal } from "@/components/editor/EditorModal";
import { ControllerPnlChart } from "@/components/bots/ControllerPnlChart";
import { DeployBotDialog } from "@/components/bots/DeployBotDialog";
import { LogsSection } from "@/components/bots/LogsSection";
import { PnlEvolutionChart } from "@/components/bots/PnlEvolutionChart";
import { ScopeTree, StatusDot } from "@/components/perf/ScopeTree";
import {
  api,
  type BotLogEntry,
  type BotSummary,
  type ControllerInfo,
  type ControllerPerformanceSnapshot,
} from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { configToYaml, CONTROLLER_HIDDEN_KEYS } from "@/lib/configYaml";
import { formatCurrencyVolume, formatCurrencyPnl, pnlColor } from "@/lib/formatters";
import {
  buildTree,
  foldLeaves,
  indexTree,
  leafFromController,
  resolveScope,
  visibleNodeIds,
  type PerfLeaf,
} from "@/lib/perf-tree";
import { aggregatePnlSeries } from "@/lib/pnl-chart";
import type { ConvertFn } from "@/lib/rates";
import { POSITIONS_BAND_KEY } from "@/lib/sessionState";
import { useViewFacts } from "@/lib/viewFacts";

function parseSide(raw: string): string {
  const dot = raw.lastIndexOf(".");
  return dot >= 0 ? raw.slice(dot + 1) : raw;
}

// ── Scope (READ: what the browser is currently reporting on) ──
//
// The sidebar is a *scope picker*, not a controller list: the same panes
// describe one controller, one bot's controllers folded together, or the whole
// fleet. Everything downstream — the header, the KPI row, the chart, the
// positions table, whether a config editor is meaningful at all — is derived
// from one node of the tree, so there is no second notion of "what is selected".
//
// A scope is a `PerfNode` id (`all`, `bot:x`, `ctrl:k`, `exec:id`), which lives
// in the URL so a scope is linkable and survives a reload (FEAT-084).

const FLEET_SCOPE = "all";

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

// ── Component ──

export function PerfBrowser({
  controllers,
  bots,
  server,
  convert,
  currencySymbol,
  snapshots = [],
  truncated = false,
}: PerfBrowserProps) {
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
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
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

  // The positions band is a disclosure, shut until asked for (FEAT-085). It
  // used to be a third of the pane's height standing open whether or not the
  // reader wanted a per-position breakdown, and the chart paid for it.
  //
  // Remembered per device rather than per scope: it says how this window is set
  // up, which is the same reason the key is KEPT across a logout (see
  // lib/sessionState).
  const [positionsOpen, setPositionsOpen] = useState(() => {
    try {
      return localStorage.getItem(POSITIONS_BAND_KEY) === "open";
    } catch {
      return false;
    }
  });
  const togglePositions = useCallback(() => {
    setPositionsOpen((open) => {
      try {
        localStorage.setItem(POSITIONS_BAND_KEY, open ? "closed" : "open");
      } catch {
        // Storage disabled: the band still opens, it just forgets overnight.
      }
      return !open;
    });
  }, []);

  const now = useSyncExternalStore(subscribeToClock, clockSnapshot, clockSnapshot);

  // ── The tree the whole page is derived from ──

  const leaves = useMemo(() => controllers.map(leafFromController), [controllers]);
  const tree = useMemo(() => buildTree(leaves, "bot", "All controllers"), [leaves]);
  const nodes = useMemo(() => indexTree(tree), [tree]);

  // The scope lives in the URL, so `?scope=ctrl:<bot>:<config id>` is a link to
  // one controller and a reload lands back on it. Written with `replace` — the
  // arrow keys walk the sidebar, and every step of that walk in the history
  // stack would make Back useless.
  const [searchParams, setSearchParams] = useSearchParams();
  const scopeId = searchParams.get("scope") || FLEET_SCOPE;
  const setScope = useCallback(
    (next: string) => {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev);
          if (next === FLEET_SCOPE) params.delete("scope");
          else params.set("scope", next);
          return params;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // A scope whose node has gone — a bot stopped, a config removed — would
  // render an empty screen with no way back, so it re-aims at the nearest
  // ancestor that survived rather than resetting to the fleet (see
  // `resolveScope`, which reads that ancestry out of the id itself).
  const effectiveScopeId = resolveScope(nodes, scopeId);
  const scope = nodes.get(effectiveScopeId) ?? tree;

  const scopedLeaves = scope.leaves;
  const activeCtrl =
    scope.kind === "controller" && scope.leaves[0]?.kind === "controller"
      ? (scope.leaves[0].source as ControllerInfo)
      : undefined;

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

  // ── What a bot scope owns: the bot itself, its logs, and stopping it ──

  const activeBot =
    scope.kind === "bot" ? bots.find((b) => b.bot_name === scope.label) : undefined;
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
    drawer === "config" && activeCtrl ? "config" : drawer === "logs" && activeBot ? "logs" : null;

  // ── Keyboard navigation over the picker, in the order it is drawn ──

  const navItems = useMemo(() => visibleNodeIds(tree, collapsed), [tree, collapsed]);
  const navIdx = navItems.indexOf(effectiveScopeId);

  const goUp = useCallback(() => {
    if (navIdx > 0) setScope(navItems[navIdx - 1]);
  }, [navIdx, navItems, setScope]);

  const goDown = useCallback(() => {
    if (navIdx >= 0 && navIdx < navItems.length - 1) setScope(navItems[navIdx + 1]);
  }, [navIdx, navItems, setScope]);

  const toggleCollapse = useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

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

  /** The live controllers under this scope, whatever level it sits at. */
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

  const aggregatedData = useMemo(
    () => (activeCtrl ? [] : aggregatePnlSeries(snapshots, scopedKeys, scopedControllers, convert)),
    [activeCtrl, snapshots, scopedKeys, scopedControllers, convert],
  );

  // Tell the chat which scope is open, while the browser is (FEAT-059).
  useViewFacts(() =>
    activeCtrl
      ? {
          label: "Controller config",
          subject: `controller "${activeCtrl.controller_name}" (${activeCtrl.trading_pair}) of bot ${activeCtrl.bot_name}`,
        }
      : {
          label: "Bot performance",
          subject:
            scope.kind === "bot"
              ? `all ${scopedLeaves.length} controllers of bot ${scope.label}`
              : `all ${scopedLeaves.length} controllers across ${tree.children.length} bots`,
        },
  );

  if (controllers.length === 0) return null;

  const configId = activeCtrl ? activeCtrl.controller_id || activeCtrl.controller_name : "";
  const chartHeight = Math.max(MIN_CHART_PX, (chartBoxH || 420) - CHART_CHROME_PX);

  return (
    <div className="flex h-full min-h-0 bg-[var(--color-bg)]">
      {/* Left sidebar: the scope picker */}
      <div
        className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
          isCompact ? "w-12" : "w-72"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2.5">
          {!isCompact && (
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Scope
            </span>
          )}
          <button
            onClick={() => setIsCompact(!isCompact)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            {isCompact ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
          </button>
        </div>

        {/* Scope list: fleet → bot → controller */}
        <div ref={sidebarRef} className="flex-1 overflow-y-auto scrollbar-thin">
          <ScopeTree
            root={tree}
            activeId={effectiveScopeId}
            collapsed={collapsed}
            onSelect={setScope}
            onToggleCollapse={toggleCollapse}
            cv={cv}
            currencySymbol={currencySymbol}
            now={now}
            compact={isCompact}
          />
        </div>

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

      {/* Main content */}
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
            ) : (
              <div className="truncate">
                <h2 className="text-sm font-semibold truncate flex items-center gap-2">
                  {scope.kind === "bot" ? (
                    <>
                      <Server className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                      {scope.label}
                    </>
                  ) : (
                    <>
                      <Layers className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                      All controllers combined
                    </>
                  )}
                </h2>
                <span className="text-[10px] text-[var(--color-text-muted)] block truncate">
                  {scopedLeaves.length} controller{scopedLeaves.length !== 1 ? "s" : ""} aggregated
                  {scope.kind === "fleet" && ` · ${tree.children.length} bot${tree.children.length !== 1 ? "s" : ""}`}
                </span>
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

            {activeCtrl && (
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
            {activeCtrl && (
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

            {/* The run history is still a table of its own until the Terminated
                population lands in the sidebar. With the tab bar gone, this
                link is the only door to it. */}
            <Link
              to="/bots?tab=runs"
              className="ml-1 flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Bot run history"
            >
              <History className="h-3.5 w-3.5" />
              Runs
            </Link>

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
          <div className="flex flex-1 flex-col min-w-0 gap-3 p-4">
            {/* Headline numbers first: the chart below is the shape of these. */}
            <div className="shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3">
              <div className="grid grid-cols-3 lg:grid-cols-6 gap-4">
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
                <Kpi
                  label="Volume"
                  value={formatCurrencyVolume(totals.volume, currencySymbol)}
                  sub={perHour(totals.volume, formatCurrencyVolume)}
                />
                {totals.capital > 0 && (
                  <Kpi
                    label="Capital Deployed"
                    value={formatCurrencyVolume(totals.capital, currencySymbol)}
                    // Turnover rather than a controller count: how hard the
                    // capital is working is the thing the two numbers beside it
                    // do not already say, and the count is on the Runtime tile.
                    sub={`${(totals.volume / totals.capital).toFixed(1)}x turnover`}
                  />
                )}
                <Kpi
                  label="Runtime"
                  value={totals.hours > 0 ? `${totals.hours.toFixed(1)}h` : "—"}
                  // Named in hours, not "2d 9h", because it is the divisor of
                  // every per-hour figure in this row — the reader can check the
                  // pace against the total without converting anything.
                  sub={
                    activeCtrl
                      ? activeCtrl.bot_name
                      : `${totals.bots} bot${totals.bots !== 1 ? "s" : ""}, ${totals.count} controller${totals.count !== 1 ? "s" : ""}`
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

            {/* The chart takes whatever vertical room is left. */}
            <div ref={chartRef} className="relative flex-1 min-h-[260px]">
              <div className="absolute inset-0">
                {activeCtrl ? (
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
                ) : aggregatedData.length >= 2 ? (
                  <PnlEvolutionChart
                    data={aggregatedData}
                    title={scope.kind === "bot" ? `${scope.label} PnL` : "Fleet PnL"}
                    pnlHeight={Math.round(chartHeight * 0.65)}
                    volumeHeight={chartHeight - Math.round(chartHeight * 0.65)}
                    currencySymbol={currencySymbol}
                    notice={
                      truncated
                        ? {
                            label: "partial history",
                            detail:
                              "This fleet has more stored history than one chart may load at once, so the series starts later than the earliest deploy.",
                          }
                        : undefined
                    }
                  />
                ) : (
                  <div className="flex h-full items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
                    <p className="text-xs text-[var(--color-text-muted)]">No performance history available</p>
                  </div>
                )}
              </div>
            </div>

            {/* The bottom band, now positions alone and shut by default
                (FEAT-085): a one-line header pinned under the chart, opening
                over it when the reader wants the breakdown. The close types
                that used to share this band are up in the strip. */}
            {positionRows.length > 0 && (
              <div
                className={`shrink-0 flex min-h-0 flex-col rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden ${
                  positionsOpen ? "max-h-[45%]" : ""
                }`}
              >
                <button
                  type="button"
                  onClick={togglePositions}
                  aria-expanded={positionsOpen}
                  data-positions-toggle
                  className="flex shrink-0 items-center gap-1.5 px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                >
                  <ChevronRight
                    className={`h-3 w-3 transition-transform ${positionsOpen ? "rotate-90" : ""}`}
                  />
                  Positions held ({positionRows.length})
                </button>
                {positionsOpen && (
                  <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin border-t border-[var(--color-border)]/60">
                    {/* A table rather than a grid of cards: at fleet scope these
                        are dozens of one-line facts, and a row per position is
                        what makes them comparable at a glance. */}
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
