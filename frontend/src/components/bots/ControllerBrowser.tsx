import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Circle,
  Layers,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Save,
  Server,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import yamlLib from "js-yaml";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { ControllerPnlChart } from "@/components/bots/ControllerPnlChart";
import { PnlEvolutionChart } from "@/components/bots/PnlEvolutionChart";
import { api, type ControllerInfo, type ControllerPerformanceSnapshot } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { configToYaml, CONTROLLER_HIDDEN_KEYS } from "@/lib/configYaml";
import { formatCurrencyVolume, formatCurrencyPnl, pnlColor } from "@/lib/formatters";
import { aggregatePnlSeries } from "@/lib/pnl-chart";
import type { ConvertFn } from "@/lib/rates";
import { useViewFacts } from "@/lib/viewFacts";

function parseSide(raw: string): string {
  const dot = raw.lastIndexOf(".");
  return dot >= 0 ? raw.slice(dot + 1) : raw;
}

/**
 * The status marker, at one size for every row.
 *
 * Both branches are `shrink-0`, and that is load-bearing rather than tidy: the
 * dot sits in a flex row beside a `truncate`d controller name, whose flex base
 * is its full max-content width. Shrinkage is distributed in proportion to
 * those bases, so a name long enough to overflow the sidebar handed the 8px
 * marker its share of the deficit and drew it a pixel or two smaller than the
 * one in the row above — the longer the name, the smaller the dot. The two
 * branches are also the same 8px as each other, so a controller does not
 * change the size of its marker on its way to stopping.
 */
function StatusDot({ status }: { status: string }) {
  const isStopping = status === "stopping";
  const color =
    status === "running"
      ? "text-[var(--color-green)]"
      : status === "stopped" || status === "error"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-yellow)]";
  return isStopping ? (
    <span className="h-2 w-2 shrink-0 animate-spin rounded-full border-[1.5px] border-[var(--color-yellow)] border-t-transparent" />
  ) : (
    <Circle className={`h-2 w-2 shrink-0 fill-current ${color}`} />
  );
}

// ── Scope (READ: what the browser is currently reporting on) ──
//
// The sidebar is a *scope picker*, not just a controller list: the same panes
// describe one controller, one bot's controllers folded together, or the whole
// fleet. Everything downstream — the header, the KPI row, the chart, the
// positions table, whether a config editor is meaningful at all — is derived
// from this one value, so there is no second notion of "what is selected".

type Scope =
  | { kind: "all" }
  | { kind: "bot"; bot: string }
  | { kind: "controller"; key: string };

function scopeId(s: Scope): string {
  return s.kind === "all" ? "all" : s.kind === "bot" ? `bot:${s.bot}` : `ctrl:${s.key}`;
}

function sameScope(a: Scope, b: Scope): boolean {
  return scopeId(a) === scopeId(b);
}

// ── Types ──

interface ControllerBrowserProps {
  controllers: ControllerInfo[];
  server: string;
  initialControllerKey: string;
  onClose: () => void;
  convert: ConvertFn;
  currencySymbol: string;
  /**
   * The fleet's performance history, already fetched by the page behind this
   * overlay. A single controller still loads its own finer series (see
   * ControllerPnlChart); the combined scopes fold *these* rows, so opening the
   * browser costs no extra request.
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
 * Everything in the chart card that is not one of the two panes: the header
 * strip, the two pane captions and the range strip below them. The panes are
 * sized from the space that is left, so the card fills its column exactly
 * instead of being a fixed 200px island in a screen-tall pane.
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

export function ControllerBrowser({
  controllers,
  server,
  initialControllerKey,
  onClose,
  convert,
  currencySymbol,
  snapshots = [],
  truncated = false,
}: ControllerBrowserProps) {
  const cv = useCallback(
    (val: number, pair: string) => {
      const quote = pair?.split("-")[1] || "USDT";
      return convert(val, quote).value;
    },
    [convert],
  );
  const fmtPnl = (val: number, pair: string) => formatCurrencyPnl(cv(val, pair), currencySymbol);
  const fmtVol = (val: number, pair: string) => formatCurrencyVolume(cv(val, pair), currencySymbol);
  const queryClient = useQueryClient();
  const [isCompact, setIsCompact] = useState(false);
  // Collapsed by default: the editor is a thing you occasionally open, and the
  // chart is the thing you came for — it gets the room until you ask otherwise.
  const [showConfig, setShowConfig] = useState(false);
  const [collapsedBots, setCollapsedBots] = useState<Set<string>>(() => new Set());
  const sidebarRef = useRef<HTMLDivElement>(null);
  const [chartRef, chartBoxH] = useMeasuredHeight<HTMLDivElement>();

  // The same composite `ActiveBotsTab` hands over as `initialControllerKey`:
  // a controller is identified by its bot *and* its config id, never the id
  // alone (CORR-241).
  const ctrlKey = useCallback((c: ControllerInfo) => controllerKey(c), []);

  const [scope, setScope] = useState<Scope>(() => ({ kind: "controller", key: initialControllerKey }));
  const [syncedInitial, setSyncedInitial] = useState(initialControllerKey);

  // The page behind the overlay can re-aim it at another controller (clicking a
  // different row). Synced while rendering rather than from an effect, so the
  // panes never paint one frame of the previous scope first — the same pattern
  // AggregatedPnlChart uses to prune its selection.
  if (syncedInitial !== initialControllerKey) {
    setSyncedInitial(initialControllerKey);
    setScope({ kind: "controller", key: initialControllerKey });
  }

  // One group per bot, controllers keeping the order the page sorted them in.
  const groups = useMemo(() => {
    const byBot = new Map<string, ControllerInfo[]>();
    for (const c of controllers) {
      const list = byBot.get(c.bot_name);
      if (list) list.push(c);
      else byBot.set(c.bot_name, [c]);
    }
    return Array.from(byBot, ([bot, ctrls]) => ({ bot, ctrls }));
  }, [controllers]);

  /** The controllers the current scope folds together. */
  const scoped = useMemo(() => {
    if (scope.kind === "all") return controllers;
    if (scope.kind === "bot") return controllers.filter((c) => c.bot_name === scope.bot);
    const one = controllers.find((c) => ctrlKey(c) === scope.key);
    return one ? [one] : [];
  }, [scope, controllers, ctrlKey]);

  // A scope whose controllers all vanished (bot stopped, config removed) would
  // render an empty screen with no way back; fall through to the fleet.
  const effectiveScope: Scope = scoped.length === 0 ? { kind: "all" } : scope;
  const effectiveScoped = scoped.length === 0 ? controllers : scoped;

  const isSingle = effectiveScope.kind === "controller";
  const activeCtrl = isSingle ? effectiveScoped[0] : undefined;

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

  // ── Keyboard navigation over the picker, in the order it is drawn ──

  const navItems = useMemo(() => {
    const items: Scope[] = [{ kind: "all" }];
    for (const g of groups) {
      items.push({ kind: "bot", bot: g.bot });
      if (!collapsedBots.has(g.bot)) {
        for (const c of g.ctrls) items.push({ kind: "controller", key: ctrlKey(c) });
      }
    }
    return items;
  }, [groups, collapsedBots, ctrlKey]);

  const navIdx = navItems.findIndex((s) => sameScope(s, effectiveScope));

  const goUp = useCallback(() => {
    if (navIdx > 0) setScope(navItems[navIdx - 1]);
  }, [navIdx, navItems]);

  const goDown = useCallback(() => {
    if (navIdx >= 0 && navIdx < navItems.length - 1) setScope(navItems[navIdx + 1]);
  }, [navIdx, navItems]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      // Skip if focus is inside CodeMirror editor (contenteditable div)
      const inEditor = e.target instanceof HTMLElement && e.target.closest(".cm-editor");
      if (inEditor && !e.metaKey && !e.ctrlKey) return;
      if (e.key === "ArrowUp") { goUp(); e.preventDefault(); }
      else if (e.key === "ArrowDown") { goDown(); e.preventDefault(); }
      else if (e.key === "Escape") { onClose(); e.preventDefault(); }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [goUp, goDown, onClose]);

  // Scroll active into view
  const activeScopeId = scopeId(effectiveScope);
  useEffect(() => {
    const el = sidebarRef.current?.querySelector("[data-active-ctrl]");
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeScopeId]);

  // ── What the scope adds up to, in display currency ──

  const totals = useMemo(() => {
    let realized = 0, unrealized = 0, total = 0, volume = 0;
    for (const c of effectiveScoped) {
      realized += cv(c.realized_pnl_quote, c.trading_pair);
      unrealized += cv(c.unrealized_pnl_quote, c.trading_pair);
      total += cv(c.global_pnl_quote, c.trading_pair);
      volume += cv(c.volume_traded, c.trading_pair);
    }
    return { realized, unrealized, total, volume };
  }, [effectiveScoped, cv]);

  /**
   * How long the scope has actually been running, and what it has put to work.
   *
   * `hours` is measured from the earliest deploy in scope to now — real elapsed
   * time, never a nominal "a day". Every per-hour figure in the KPI row divides
   * by this, so a fleet up for 8 hours and one up for 8 days are comparable
   * even though their totals are not.
   *
   * `capital` is the sum of each controller's declared `total_amount_quote`,
   * the same field the `bot_report` routine reports as "Capital Deployed". Not
   * every controller type declares one; the tile is dropped rather than shown
   * as zero when none in scope does.
   */
  const now = useSyncExternalStore(subscribeToClock, clockSnapshot, clockSnapshot);
  const scopeFacts = useMemo(() => {
    let earliest: number | undefined;
    let capital = 0;
    const bots = new Set<string>();
    let positions = 0;
    for (const c of effectiveScoped) {
      bots.add(c.bot_name);
      positions += c.positions_summary?.length ?? 0;
      const amount = Number(c.config?.total_amount_quote ?? 0);
      if (Number.isFinite(amount) && amount > 0) capital += cv(amount, c.trading_pair);
      const ms = c.deployed_at ? Date.parse(c.deployed_at) : NaN;
      if (!Number.isNaN(ms) && (earliest === undefined || ms < earliest)) earliest = ms;
    }
    const hours = earliest === undefined ? 0 : Math.max(0, (now - earliest) / 3_600_000);
    return { hours, capital, bots: bots.size, positions };
  }, [effectiveScoped, cv, now]);

  /**
   * A per-hour pace, or nothing.
   *
   * Nothing is the right answer for a scope with no known deploy time: a total
   * divided by a runtime we do not have is a made-up rate, and it would be
   * indistinguishable on screen from a measured one.
   */
  const perHour = (total: number, fmt: (v: number, symbol?: string) => string) =>
    scopeFacts.hours > 0 ? `${fmt(total / scopeFacts.hours, currencySymbol)}/hr` : undefined;

  /** Per-bot / per-controller totals for the picker rows, in display currency. */
  const rowTotals = useMemo(() => {
    const byBot = new Map<string, { pnl: number; volume: number }>();
    let fleetPnl = 0, fleetVolume = 0;
    for (const c of controllers) {
      const pnl = cv(c.global_pnl_quote, c.trading_pair);
      const vol = cv(c.volume_traded, c.trading_pair);
      fleetPnl += pnl;
      fleetVolume += vol;
      const acc = byBot.get(c.bot_name) ?? { pnl: 0, volume: 0 };
      acc.pnl += pnl;
      acc.volume += vol;
      byBot.set(c.bot_name, acc);
    }
    return { byBot, fleetPnl, fleetVolume };
  }, [controllers, cv]);

  const closeTypeCounts = useMemo(() => {
    const merged: Record<string, number> = {};
    for (const c of effectiveScoped) {
      for (const [type, count] of Object.entries(c.close_type_counts || {})) {
        merged[type] = (merged[type] ?? 0) + count;
      }
    }
    return Object.entries(merged).sort((a, b) => b[1] - a[1]);
  }, [effectiveScoped]);

  const positionRows = useMemo<PositionRow[]>(() => {
    const rows: PositionRow[] = [];
    for (const c of effectiveScoped) {
      const label = c.controller_id || c.controller_name;
      (c.positions_summary || []).forEach((pos, i) => {
        const pair = String(pos.trading_pair || c.trading_pair || "");
        rows.push({
          id: `${ctrlKey(c)}#${i}`,
          ctrlLabel: label,
          pair,
          connector: String(pos.connector_name || pos.connector || c.connector || ""),
          side: parseSide(String(pos.side || "")),
          amount: num(pos.amount),
          breakeven: num(pos.breakeven_price),
          realized: cv(Number(pos.realized_pnl_quote || 0), pair),
          unrealized: cv(Number(pos.unrealized_pnl_quote || 0), pair),
          volume: cv(Number(pos.volume_traded_quote || pos.volume_traded || 0), pair),
          extras: Object.entries(pos).filter(([k]) => !POSITION_PRIMARY_KEYS.has(k)),
        });
      });
    }
    return rows;
  }, [effectiveScoped, ctrlKey, cv]);

  /** Extra per-position fields, as columns, so the table stays one grid. */
  const extraColumns = useMemo(() => {
    const seen: string[] = [];
    for (const row of positionRows) {
      for (const [k] of row.extras) if (!seen.includes(k)) seen.push(k);
    }
    return seen;
  }, [positionRows]);

  // ── The aggregated series, folded from the fleet history the page already has ──

  const scopedKeys = useMemo(
    () => new Set(effectiveScoped.map((c) => ctrlKey(c))),
    [effectiveScoped, ctrlKey],
  );

  const aggregatedData = useMemo(
    () => (isSingle ? [] : aggregatePnlSeries(snapshots, scopedKeys, effectiveScoped, convert)),
    [isSingle, snapshots, scopedKeys, effectiveScoped, convert],
  );

  // Tell the chat which controller is open, while the browser is (FEAT-059).
  useViewFacts(() =>
    activeCtrl
      ? {
          label: "Controller config",
          subject: `controller "${activeCtrl.controller_name}" (${activeCtrl.trading_pair}) of bot ${activeCtrl.bot_name}`,
        }
      : {
          label: "Bot performance",
          subject:
            effectiveScope.kind === "bot"
              ? `all ${effectiveScoped.length} controllers of bot ${effectiveScope.bot}`
              : `all ${effectiveScoped.length} controllers across ${groups.length} bots`,
        },
  );

  if (controllers.length === 0) return null;

  const configId = activeCtrl ? activeCtrl.controller_id || activeCtrl.controller_name : "";
  const chartHeight = Math.max(MIN_CHART_PX, (chartBoxH || 420) - CHART_CHROME_PX);
  const configOpen = showConfig && !!activeCtrl;

  // ── Picker rows ──

  const scopeRowClass = (active: boolean) =>
    `w-full text-left transition-all border-l-[3px] ${
      active
        ? "bg-[var(--color-primary)]/15 border-l-[var(--color-primary)] shadow-[inset_0_0_0_1px_var(--color-primary)]/10"
        : "border-l-transparent hover:bg-[var(--color-surface-hover)]"
    }`;

  return (
    <div className="fixed inset-0 z-50 flex bg-[var(--color-bg)]">
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
          {/* All controllers combined */}
          {(() => {
            const active = effectiveScope.kind === "all";
            if (isCompact) {
              return (
                <button
                  onClick={() => setScope({ kind: "all" })}
                  {...(active ? { "data-active-ctrl": true } : {})}
                  className={`flex w-full items-center justify-center py-3 ${
                    active ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]" : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title="All controllers"
                >
                  <Layers className="h-3.5 w-3.5" />
                </button>
              );
            }
            return (
              <button
                onClick={() => setScope({ kind: "all" })}
                {...(active ? { "data-active-ctrl": true } : {})}
                className={`${scopeRowClass(active)} px-3 py-2.5`}
              >
                <div className="flex items-center gap-2">
                  <Layers className={`h-3.5 w-3.5 shrink-0 ${active ? "text-[var(--color-primary)]" : "text-[var(--color-text-muted)]"}`} />
                  <span className={`text-xs ${active ? "font-semibold text-[var(--color-text)]" : "font-medium text-[var(--color-text-muted)]"}`}>
                    All controllers
                  </span>
                  <span className="ml-auto tabular-nums text-xs font-semibold" style={{ color: pnlColor(rowTotals.fleetPnl) }}>
                    {formatCurrencyPnl(rowTotals.fleetPnl, currencySymbol)}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 pl-5.5 text-[10px] text-[var(--color-text-muted)]">
                  <span>{controllers.length} controllers · {groups.length} bot{groups.length !== 1 ? "s" : ""}</span>
                  <span className="ml-auto tabular-nums">{formatCurrencyVolume(rowTotals.fleetVolume, currencySymbol)}</span>
                </div>
              </button>
            );
          })()}

          {groups.map((g) => {
            const botActive = effectiveScope.kind === "bot" && effectiveScope.bot === g.bot;
            const collapsed = collapsedBots.has(g.bot);
            const agg = rowTotals.byBot.get(g.bot) ?? { pnl: 0, volume: 0 };
            return (
              <div key={g.bot} className="border-t border-[var(--color-border)]/40">
                {/* Bot row: pick the bot, or fold its controllers away */}
                {!isCompact && (
                  <div className={`${scopeRowClass(botActive)} flex items-stretch`}>
                    <button
                      onClick={() =>
                        setCollapsedBots((prev) => {
                          const next = new Set(prev);
                          if (next.has(g.bot)) next.delete(g.bot);
                          else next.add(g.bot);
                          return next;
                        })
                      }
                      className="px-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                      title={collapsed ? "Show controllers" : "Hide controllers"}
                    >
                      {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    </button>
                    <button
                      onClick={() => setScope({ kind: "bot", bot: g.bot })}
                      {...(botActive ? { "data-active-ctrl": true } : {})}
                      className="flex-1 min-w-0 py-2 pr-3 text-left"
                    >
                      <div className="flex items-center gap-1.5">
                        <Server className={`h-3 w-3 shrink-0 ${botActive ? "text-[var(--color-primary)]" : "text-[var(--color-text-muted)]"}`} />
                        <span
                          className={`truncate text-[11px] ${botActive ? "font-semibold text-[var(--color-text)]" : "font-medium text-[var(--color-text-muted)]"}`}
                          title={g.bot}
                        >
                          {g.bot}
                        </span>
                        <span className="ml-auto shrink-0 tabular-nums text-[11px] font-semibold" style={{ color: pnlColor(agg.pnl) }}>
                          {formatCurrencyPnl(agg.pnl, currencySymbol)}
                        </span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 pl-4.5 text-[10px] text-[var(--color-text-muted)]">
                        <span>{g.ctrls.length} controller{g.ctrls.length !== 1 ? "s" : ""}</span>
                        <span className="ml-auto tabular-nums">{formatCurrencyVolume(agg.volume, currencySymbol)}</span>
                      </div>
                    </button>
                  </div>
                )}

                {(isCompact || !collapsed) &&
                  g.ctrls.map((c) => {
                    const key = ctrlKey(c);
                    const isActive = effectiveScope.kind === "controller" && effectiveScope.key === key;
                    const killed = c.config?.manual_kill_switch === true;
                    const ctrlStopping = c.status === "stopping";

                    if (isCompact) {
                      return (
                        <button
                          key={key}
                          onClick={() => setScope({ kind: "controller", key })}
                          {...(isActive ? { "data-active-ctrl": true } : {})}
                          className={`flex w-full items-center justify-center py-3 transition-colors ${
                            isActive
                              ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                              : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                          }`}
                          title={c.controller_name}
                        >
                          <StatusDot status={ctrlStopping ? "stopping" : killed ? "stopped" : c.status} />
                        </button>
                      );
                    }

                    return (
                      <button
                        key={key}
                        onClick={() => setScope({ kind: "controller", key })}
                        {...(isActive ? { "data-active-ctrl": true } : {})}
                        className={`${scopeRowClass(isActive)} py-2 pl-5 pr-3`}
                      >
                        <div className="flex items-center gap-2">
                          <StatusDot status={ctrlStopping ? "stopping" : killed ? "stopped" : c.status} />
                          <span
                            className={`truncate text-xs ${isActive ? "font-semibold text-[var(--color-text)]" : "font-medium text-[var(--color-text-muted)]"}`}
                            title={c.controller_id || c.controller_name}
                          >
                            {c.controller_id || c.controller_name}
                          </span>
                          <span className="ml-auto shrink-0 tabular-nums text-xs font-semibold" style={{ color: pnlColor(c.global_pnl_quote) }}>
                            {fmtPnl(c.global_pnl_quote, c.trading_pair)}
                          </span>
                        </div>
                        <div className="mt-0.5 flex items-center gap-2 pl-4 text-[10px] text-[var(--color-text-muted)]">
                          {c.trading_pair && <span>{c.trading_pair}</span>}
                          {/* Volume is what tells two similarly-profitable configs
                              apart at a glance, so it rides beside the PnL. */}
                          <span className="ml-auto tabular-nums">{fmtVol(c.volume_traded, c.trading_pair)}</span>
                        </div>
                      </button>
                    );
                  })}
              </div>
            );
          })}
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
              <kbd className="inline-flex h-4 items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-1 text-[8px] font-medium">
                esc
              </kbd>
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
                  {effectiveScope.kind === "bot" ? (
                    <>
                      <Server className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                      {effectiveScope.bot}
                    </>
                  ) : (
                    <>
                      <Layers className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                      All controllers combined
                    </>
                  )}
                </h2>
                <span className="text-[10px] text-[var(--color-text-muted)] block truncate">
                  {effectiveScoped.length} controller{effectiveScoped.length !== 1 ? "s" : ""} aggregated
                  {effectiveScope.kind === "all" && ` · ${groups.length} bot${groups.length !== 1 ? "s" : ""}`}
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
                onClick={() => setShowConfig((v) => !v)}
                className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                  configOpen
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                }`}
                title={configOpen ? "Hide config" : "Show config"}
              >
                <SlidersHorizontal className="h-3.5 w-3.5" />
                Config
              </button>
            )}
            <button
              onClick={onClose}
              className="ml-1 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
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
                  value={formatCurrencyPnl(totals.total, currencySymbol)}
                  color={pnlColor(totals.total)}
                  // The return % is per-controller — each is measured against its
                  // own notional — so it rides along only where it means
                  // something. Summing or averaging them across a fold would
                  // report a return nobody earned.
                  sub={[
                    perHour(totals.total, formatCurrencyPnl),
                    activeCtrl
                      ? `${activeCtrl.global_pnl_pct >= 0 ? "+" : ""}${activeCtrl.global_pnl_pct.toFixed(2)}%`
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
                    scopeFacts.positions === 0
                      ? "no open positions"
                      : `${scopeFacts.positions} position${scopeFacts.positions !== 1 ? "s" : ""}`
                  }
                />
                <Kpi
                  label="Volume"
                  value={formatCurrencyVolume(totals.volume, currencySymbol)}
                  sub={perHour(totals.volume, formatCurrencyVolume)}
                />
                {scopeFacts.capital > 0 && (
                  <Kpi
                    label="Capital Deployed"
                    value={formatCurrencyVolume(scopeFacts.capital, currencySymbol)}
                    // Turnover rather than a controller count: how hard the
                    // capital is working is the thing the two numbers beside it
                    // do not already say, and the count is on the Runtime tile.
                    sub={`${(totals.volume / scopeFacts.capital).toFixed(1)}x turnover`}
                  />
                )}
                <Kpi
                  label="Runtime"
                  value={scopeFacts.hours > 0 ? `${scopeFacts.hours.toFixed(1)}h` : "—"}
                  // Named in hours, not "2d 9h", because it is the divisor of
                  // every per-hour figure in this row — the reader can check the
                  // pace against the total without converting anything.
                  sub={
                    activeCtrl
                      ? activeCtrl.bot_name
                      : `${scopeFacts.bots} bot${scopeFacts.bots !== 1 ? "s" : ""}, ${effectiveScoped.length} controller${effectiveScoped.length !== 1 ? "s" : ""}`
                  }
                />
              </div>
            </div>

            {/* The chart takes whatever vertical room is left. */}
            <div ref={chartRef} className="relative flex-1 min-h-[260px]">
              <div className="absolute inset-0">
                {activeCtrl ? (
                  <ControllerPnlChart
                    // Keyed by bot + config id: two bots can be running this very
                    // config, and a key that did not tell them apart would keep the
                    // sibling's mounted state when the user switches (CORR-241).
                    key={ctrlKey(activeCtrl)}
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
                    title={effectiveScope.kind === "bot" ? `${effectiveScope.bot} PnL` : "Fleet PnL"}
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

            {/* Close types + positions share the bottom band and scroll inside it. */}
            {(closeTypeCounts.length > 0 || positionRows.length > 0) && (
              <div className="shrink-0 max-h-[38%] overflow-y-auto scrollbar-thin space-y-3 pr-0.5">
                {closeTypeCounts.length > 0 && (
                  <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 space-y-2">
                    <h3 className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                      Close Types
                    </h3>
                    <div className="flex flex-wrap gap-1.5">
                      {closeTypeCounts.map(([type, count]) => (
                        <span
                          key={type}
                          className="inline-flex items-center gap-1 rounded-md bg-[var(--color-bg)] px-2 py-0.5 text-[11px] border border-[var(--color-border)]/50"
                        >
                          <span className="text-[var(--color-text-muted)]">{parseSide(type)}</span>
                          <span className="font-semibold">{count}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {positionRows.length > 0 && (
                  <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
                    <h3 className="px-3 pt-2.5 pb-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                      Positions held ({positionRows.length})
                    </h3>
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

          {/* Right drawer: the config of the one controller in scope */}
          {configOpen && activeCtrl && (
            <div className="w-[380px] xl:w-[440px] shrink-0 border-l border-[var(--color-border)] flex flex-col bg-[var(--color-surface)]">
              <YamlConfigEditor
                // Same: an editor keyed on the config id alone kept its unsaved
                // buffer when the user switched to the other bot running it.
                key={ctrlKey(activeCtrl)}
                config={activeCtrl.config || {}}
                server={server}
                configId={configId}
                botName={activeCtrl.bot_name}
                onSaved={() => queryClient.invalidateQueries({ queryKey: ["bots", server] })}
                onCollapse={() => setShowConfig(false)}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
