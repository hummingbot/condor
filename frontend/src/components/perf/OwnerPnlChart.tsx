import { useCallback, useId, useMemo, useState } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  Rectangle,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SetURLSearchParams } from "react-router-dom";

import { PnlRangeStrip } from "@/components/bots/PnlRangeStrip";

import type { ScopeOwner } from "@/components/perf/scopeOwners";
import {
  formatAxisCurrency,
  formatAxisTime,
  formatCurrencyPnl,
  formatDateTime,
  pnlTextClass,
} from "@/lib/formatters";
import {
  ownerDataKey,
  parseBaseline,
  parseBasis,
  rebaseRows,
  seriesColor,
  type FloorChartRow,
} from "@/lib/owner-series";
import {
  AXIS_WIDTH,
  PANE_MARGIN_RIGHT,
  PANE_PAD_X,
  PNL_SERIES_COLORS,
  chartBucketMs,
  formatBucketLabel,
  positionAreaExtent,
  positionAxisDomain,
  resolveTimeRange,
  sliceToRange,
  volumeBarWidth,
  zeroGradientOffset,
  type PnlChartPoint,
  type TimeRange,
} from "@/lib/pnl-chart";
import { getThemeColors } from "@/lib/theme-colors";

/**
 * A scope's PnL, one line per child of it (FEAT-112, rehosted by FEAT-116).
 *
 * It was `FloorChart`, and it drew one line per agent because the page it was
 * on was about agents. The lines are whatever the tree's next level is now —
 * agents at the fleet scope, bots inside an agent, pairs under `?groupBy=pair`
 * — which is the same picture with the special case taken out of it. Which
 * children those are is {@link scopeOwners}' answer; everything here is about
 * drawing N series that already exist.
 *
 * A **sibling** of `PnlEvolutionChart`, not a change to it. That chart is 1050
 * lines built on fixed `dataKey` strings, a legend grouped by pane and a
 * two-pane geometry contract; adding an arbitrary series dimension to it would
 * touch the most-used chart in the app. What is shared instead is everything
 * *pure*: the axis gutter, the pane insets, the bucket and bar geometry, the
 * position axis rules, the time-range grammar and the range strip.
 *
 * The one contract that must not be broken: **both panes reserve `AXIS_WIDTH`
 * on the left and on the right**, or their plot areas differ and the synced
 * cursor in one points at a different instant than the other. Nothing throws
 * when that happens.
 *
 * The Total line is not asserted to equal the sum of the owner lines — it is
 * folded by the same function over the union of their keys, and
 * `aggregatePnlSeries` forward-fills per controller, so the equality is a
 * property of the fold (see `lib/owner-series`).
 *
 * Series visibility is **scope-local state**, deliberately not the module-level
 * `localStorage` store in `lib/pnl-chart`: that store's keys are the fixed
 * `PnlSeriesKey` union and its scope is every chart on the device, neither of
 * which fits a set of series named after this install's agents and bots.
 */
export function OwnerPnlChart({
  owners,
  rows,
  keys,
  symbol,
  net,
  capital,
  title,
  height,
  params,
  setParams,
}: {
  /** The scope's children, in the tree's own order — labels, and what Relative divides by. */
  owners: readonly ScopeOwner[];
  /** The merged rows, absolute and measured from inception. */
  rows: FloorChartRow[];
  /** The owner keys, in the order the tree lists them. */
  keys: readonly string[];
  /** The display currency's symbol, so every screen prints one currency. */
  symbol: string;
  /** The scope's own folded net — what the line's end is checked against. */
  net: number;
  /** The scope's own declared capital — what Relative measures the Total against. */
  capital: number;
  /** `Fleet PnL by agent`, and so on: the browser names the level. */
  title: string;
  /** The room the report column has left, split between the two panes. */
  height: number;
  params: URLSearchParams;
  setParams: SetURLSearchParams;
}) {
  const instanceId = useId().replace(/[^a-zA-Z0-9]/g, "");
  const posGradientId = `ownerPos${instanceId}`;

  const basis = parseBasis(params.get("basis"));
  const from = parseBaseline(params.get("from"));
  const window = parseWindow(params.get("range"));

  const set = useCallback(
    (key: string, value: string, fallback: string) => {
      const next = new URLSearchParams(params);
      if (value === fallback) next.delete(key);
      else next.set(key, value);
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  // A drag on the strip is a finer selection than the window chips express, so
  // it lives beside them rather than in the URL: the chips say which window is
  // loaded, the strip says which part of it is drawn.
  const [drag, setDrag] = useState<TimeRange | null>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  const range = drag ?? window.range;
  const [viewStart, viewEnd] = resolveTimeRange(rows as PnlChartPoint[], range);
  const visible = useMemo(
    () => sliceToRange(rows as PnlChartPoint[], viewStart, viewEnd) as FloorChartRow[],
    [rows, viewStart, viewEnd],
  );

  /** Each line's declared capital, and the scope's own — what Relative divides by. */
  const capitalOf = useMemo(() => {
    const by: Record<string, number> = { total: capital };
    for (const owner of owners) by[owner.key] = owner.capital;
    return by;
  }, [owners, capital]);

  const { rows: drawnRows, unplottable } = useMemo(
    () => rebaseRows(visible, keys, { basis, from, capital: capitalOf }),
    [visible, keys, basis, from, capitalOf],
  );
  const muted = useMemo(() => new Set(unplottable.map((u) => u.key)), [unplottable]);

  const labels = useMemo(
    () => new Map(owners.map((owner) => [owner.key, owner.label])),
    [owners],
  );

  const latest = drawnRows.length > 0 ? drawnRows[drawnRows.length - 1] : null;
  const tc = getThemeColors();

  const fmt = useCallback(
    (v: number) =>
      basis === "rel" ? `${v.toFixed(2)}%` : formatCurrencyPnl(v, symbol),
    [basis, symbol],
  );
  const fmtAxis = useCallback(
    (v: number) =>
      basis === "rel" ? `${v.toFixed(1)}%` : formatAxisCurrency(v, symbol, "pnl"),
    [basis, symbol],
  );
  const fmtVolAxis = useCallback(
    (v: number) => formatAxisCurrency(v, symbol, "volume"),
    [symbol],
  );
  const spanMs =
    visible.length > 1 ? visible[visible.length - 1].time - visible[0].time : 0;
  const fmtTimeAxis = useCallback((v: number) => formatAxisTime(v, spanMs), [spanMs]);

  // ── The activity pane (step 6) ──
  const [activityWidth, setActivityWidth] = useState(0);
  const onActivityResize = useCallback((width: number) => setActivityWidth(width), []);
  const bucketMs = useMemo(() => chartBucketMs(visible as PnlChartPoint[]), [visible]);
  const bucketLabel = useMemo(() => formatBucketLabel(bucketMs), [bucketMs]);
  const barWidth = volumeBarWidth(
    activityWidth - 2 * AXIS_WIDTH - PANE_MARGIN_RIGHT,
    spanMs,
    bucketMs,
  );
  // recharts sizes a bar on a numeric axis from the *smallest* gap between two
  // points, and this series always has one far smaller than the rest — the live
  // "now" point lands a fraction of a bucket after the last snapshot. Left
  // alone every bar thins to a hairline and thickens again as snapshots land.
  const volumeBar = useCallback(
    (props: { x: number; y: number; width: number; height: number; fill?: string }) => {
      const width = barWidth ?? props.width;
      return (
        <Rectangle
          x={props.x + props.width / 2 - width / 2}
          y={props.y}
          width={width}
          height={props.height}
          fill={props.fill}
          fillOpacity={0.45}
          radius={[2, 2, 0, 0]}
          stroke="none"
        />
      );
    },
    [barWidth],
  );
  const hasPosition = rows.some((row) => row.position !== 0);
  const positionDomain = useMemo(
    () => positionAxisDomain(visible as PnlChartPoint[]),
    [visible],
  );
  const positionZeroOffset = useMemo(
    () => zeroGradientOffset(positionAreaExtent(visible as PnlChartPoint[])),
    [visible],
  );

  // ── The stated gap between the chart and the strip ──
  //
  // The chart is built from controller *history*; an unattached executor in an
  // agent's spine has no snapshots and so contributes to the fold and not to the
  // line. Named on screen in the shape `reconcile` already uses for
  // *unaccounted*, rather than papered over — if it turns out to be large in
  // practice, the follow-up is `executorSeries` contributing closed outcomes as
  // a second source, which `lib/perf-history` already knows how to arbitrate.
  const lastAbsolute = rows.length > 0 ? rows[rows.length - 1].total : null;
  const gap = lastAbsolute === null ? 0 : net - lastAbsolute;
  const showGap =
    lastAbsolute !== null && Math.abs(gap) > Math.max(0.01, Math.abs(net) * 0.005);

  const fullSpanMs = rows.length > 1 ? rows[rows.length - 1].time - rows[0].time : 0;
  const selectRange = useCallback(
    (start: number, end: number, atLiveEdge: boolean) => {
      if (atLiveEdge && start <= (rows[0]?.time ?? 0)) return setDrag(null);
      setDrag(atLiveEdge ? { start: null, end: null, trailing: end - start } : { start, end });
    },
    [rows],
  );

  const toggle = useCallback(
    (key: string) =>
      setHidden((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        return next;
      }),
    [],
  );

  const drawn = keys.filter((key) => !hidden.has(key) && !muted.has(key));
  const totalDrawn = !hidden.has("total") && !muted.has("total");

  // The same 65/35 split `PnlEvolutionChart` takes, off the same measured box,
  // so switching between the aggregate chart and this one does not resize the
  // report column under the reader.
  const pnlHeight = Math.round(height * 0.65);
  const activityHeight = height - pnlHeight;

  return (
    <div
      data-owner-chart
      className="h-full overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
    >
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            {title}
          </p>
          <LegendChip
            label="Total"
            color={tc.up}
            // A dash, never a number, when the Total is one of the series
            // Relative cannot measure: `rebaseRows` removes the field rather
            // than writing an infinity into it.
            value={
              latest && typeof latest.total === "number" ? fmt(latest.total) : "—"
            }
            drawn={totalDrawn}
            strong
            onToggle={() => toggle("total")}
          />
          {keys.map((key, index) => (
            <LegendChip
              key={key}
              test={key}
              label={labels.get(key) ?? key}
              color={seriesColor(index)}
              value={
                latest && typeof latest[ownerDataKey(key)] === "number"
                  ? fmt(latest[ownerDataKey(key)])
                  : "—"
              }
              drawn={!hidden.has(key) && !muted.has(key)}
              onToggle={() => toggle(key)}
            />
          ))}
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Toggle
            options={[
              { value: "abs", label: "Absolute" },
              { value: "rel", label: "Relative" },
            ]}
            active={basis}
            onPick={(value) => set("basis", value, "abs")}
          />
          <Toggle
            options={[
              { value: "inception", label: "Inception" },
              { value: "window", label: "Window" },
            ]}
            active={from}
            onPick={(value) => set("from", value, "inception")}
          />
          <Toggle
            options={WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
            active={window.value}
            onPick={(value) => {
              setDrag(null);
              set("range", value, "all");
            }}
          />
        </div>
      </div>

      {/* An owner with no declared capital is listed and not drawn. Dividing by
          zero would print an infinity or a zero that both read as a fact —
          `attributedMoney`'s rule, one level up: no statement is not `0`. */}
      {unplottable.length > 0 && (
        <p
          data-owner-unplottable
          className="border-b border-[var(--color-border)] px-3 py-1 text-[10px] text-[var(--color-text-muted)]"
        >
          Not plotted in Relative — no declared capital to measure against:{" "}
          {unplottable
            .map((u) => (u.key === "total" ? "Total" : (labels.get(u.key) ?? u.key)))
            .join(", ")}
          .
        </p>
      )}

      {showGap && (
        <p
          data-owner-gap
          className="border-b border-[var(--color-border)] px-3 py-1 text-[10px] text-[var(--color-yellow)]"
          title="Records with no controller history — a standalone executor, for instance — are in the fold and cannot be in the line."
        >
          The line ends {formatCurrencyPnl(-gap, symbol)} from the strip's net:{" "}
          {formatCurrencyPnl(gap, symbol)} of the fold has no controller history to
          be drawn from.
        </p>
      )}

      {drawnRows.length === 0 ? (
        <p
          data-owner-chart-empty
          className="px-3 py-12 text-center text-sm text-[var(--color-text-muted)]"
        >
          No performance history yet.
        </p>
      ) : (
        <>
          <div
            style={{ paddingLeft: PANE_PAD_X, paddingRight: PANE_PAD_X }}
            data-pane="pnl"
          >
            <ResponsiveContainer width="100%" height={pnlHeight}>
              <ComposedChart
                data={drawnRows}
                margin={{ top: 12, right: PANE_MARGIN_RIGHT, left: 0, bottom: 0 }}
                syncId={instanceId}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--color-border)"
                  strokeOpacity={0.5}
                />
                <XAxis
                  dataKey="time"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={fmtTimeAxis}
                  tick={false}
                  stroke="var(--color-border)"
                  tickLine={false}
                  height={1}
                />
                <YAxis
                  tickFormatter={fmtAxis}
                  tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
                  stroke="var(--color-border)"
                  tickLine={false}
                  axisLine={false}
                  width={AXIS_WIDTH}
                />
                {/* The mirror of the activity pane's position axis. Both panes
                    must reserve the same gutters or they desync. */}
                <YAxis
                  yAxisId="spacer"
                  orientation="right"
                  tick={false}
                  tickLine={false}
                  axisLine={false}
                  width={AXIS_WIDTH}
                />
                <ReferenceLine
                  y={0}
                  stroke="var(--color-text-muted)"
                  strokeOpacity={0.3}
                  strokeDasharray="4 4"
                />
                <Tooltip
                  content={
                    <OwnerTooltip
                      labels={labels}
                      keys={drawn}
                      format={fmt}
                      showTotal={totalDrawn}
                      visible={!scrubbing}
                    />
                  }
                />
                {drawn.map((key) => (
                  <Line
                    key={key}
                    type="monotone"
                    dataKey={ownerDataKey(key)}
                    name={labels.get(key) ?? key}
                    stroke={seriesColor(keys.indexOf(key))}
                    strokeWidth={1.5}
                    dot={false}
                    // A gap is the truth for an owner that had not started yet.
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                ))}
                {totalDrawn && (
                  <Line
                    type="monotone"
                    dataKey="total"
                    name="Total"
                    stroke={tc.up}
                    strokeWidth={2.5}
                    strokeOpacity={0.75}
                    dot={false}
                    isAnimationActive={false}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* The activity pane: the fleet's flow and its book. Both are
              fleet-level and come from the single total series, never per owner
              — `volumeDelta` is a per-bucket flow and a forward fill would
              misplace it (see `mergeOwnerRows`). */}
          <div
            style={{ paddingLeft: PANE_PAD_X, paddingRight: PANE_PAD_X }}
            data-pane="activity"
            className="border-t border-[var(--color-border)]"
          >
            <ResponsiveContainer width="100%" height={activityHeight} onResize={onActivityResize}>
              <ComposedChart
                data={drawnRows}
                margin={{ top: 4, right: PANE_MARGIN_RIGHT, left: 0, bottom: 4 }}
                syncId={instanceId}
              >
                {hasPosition && (
                  <defs>
                    <linearGradient id={posGradientId} x1="0" y1="0" x2="0" y2="1">
                      <stop offset={positionZeroOffset} stopColor={tc.up} />
                      <stop offset={positionZeroOffset} stopColor={tc.down} />
                    </linearGradient>
                  </defs>
                )}
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--color-border)"
                  strokeOpacity={0.5}
                />
                <XAxis
                  dataKey="time"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={fmtTimeAxis}
                  tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
                  stroke="var(--color-border)"
                  tickLine={false}
                />
                <YAxis
                  yAxisId="vol"
                  tickFormatter={fmtVolAxis}
                  tick={{ fontSize: 10, fill: PNL_SERIES_COLORS.volume }}
                  stroke="var(--color-border)"
                  tickLine={false}
                  axisLine={false}
                  width={AXIS_WIDTH}
                />
                <YAxis
                  yAxisId="pos"
                  orientation="right"
                  domain={positionDomain}
                  tickFormatter={fmtVolAxis}
                  tick={
                    hasPosition ? { fontSize: 10, fill: PNL_SERIES_COLORS.position } : false
                  }
                  stroke="var(--color-border)"
                  tickLine={false}
                  axisLine={false}
                  width={AXIS_WIDTH}
                />
                <Bar
                  yAxisId="vol"
                  dataKey="volumeDelta"
                  name={bucketLabel ? `Traded / ${bucketLabel}` : "Traded"}
                  fill={PNL_SERIES_COLORS.volume}
                  shape={volumeBar}
                  isAnimationActive={false}
                />
                {hasPosition && (
                  <Area
                    yAxisId="pos"
                    type="monotone"
                    dataKey="position"
                    name="Net position"
                    baseValue={0}
                    stroke={PNL_SERIES_COLORS.position}
                    strokeWidth={1.5}
                    fill={`url(#${posGradientId})`}
                    fillOpacity={0.22}
                    dot={false}
                    isAnimationActive={false}
                  />
                )}
                {hasPosition && (
                  <ReferenceLine
                    yAxisId="pos"
                    y={0}
                    stroke={PNL_SERIES_COLORS.position}
                    strokeOpacity={0.45}
                    strokeDasharray="4 4"
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* Below the shared time axis it operates on and inset to the same
              plot area, so a column on it is the column above it. */}
          {fullSpanMs > 0 && (
            <PnlRangeStrip
              data={rows as PnlChartPoint[]}
              start={viewStart}
              end={viewEnd}
              color={tc.up}
              onSelect={selectRange}
              onScrub={setScrubbing}
            />
          )}

          <p className="border-t border-[var(--color-border)] px-3 py-1 text-[10px] text-[var(--color-text-muted)]">
            {bucketLabel ? `Bars are volume traded per ${bucketLabel} bucket. ` : ""}
            The lines are folded from controller performance history, one call per
            line — the same fold this page draws when you walk into one of them
            with <code className="font-mono">?scope=</code>.
          </p>
        </>
      )}
    </div>
  );
}

/**
 * One legend entry, doubling as the visibility toggle.
 *
 * `PnlEvolutionChart`'s `LegendEntry` pattern; the store behind it is this
 * chart's own state rather than the device-wide one, because these series are
 * named after whatever the scope's children happen to be.
 */
function LegendChip({
  test,
  label,
  color,
  value,
  drawn,
  strong = false,
  onToggle,
}: {
  test?: string;
  label: string;
  color: string;
  value: string;
  drawn: boolean;
  strong?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      data-owner-legend={test ?? "total"}
      data-drawn={drawn}
      onClick={onToggle}
      title={drawn ? `Hide ${label}` : `Show ${label}`}
      className={`flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px] tabular-nums transition-opacity hover:bg-[var(--color-surface-hover)] ${
        drawn ? "" : "opacity-40"
      }`}
    >
      <span
        aria-hidden="true"
        className="h-0.5 w-3.5 shrink-0 rounded-full"
        style={{ background: color }}
      />
      <span className={`max-w-[10rem] truncate ${strong ? "font-semibold" : ""}`}>
        {label}
      </span>
      <span className="font-mono text-[var(--color-text-muted)]">{value}</span>
    </button>
  );
}

function Toggle<T extends string>({
  options,
  active,
  onPick,
}: {
  options: readonly { value: T; label: string }[];
  active: T;
  onPick: (value: T) => void;
}) {
  return (
    <div className="flex items-center gap-0.5 rounded border border-[var(--color-border)] p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          data-owner-toggle={option.value}
          data-active={option.value === active}
          onClick={() => onPick(option.value)}
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
            option.value === active
              ? "bg-[var(--color-primary)] text-[var(--on-primary)]"
              : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

interface TooltipPayload {
  dataKey?: string | number;
  value?: number;
}

function OwnerTooltip({
  active,
  payload,
  label,
  labels,
  keys,
  format,
  showTotal,
  visible,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
  label?: number;
  labels: Map<string, string>;
  keys: readonly string[];
  format: (value: number) => string;
  showTotal: boolean;
  visible: boolean;
}) {
  if (!active || !visible || !payload || payload.length === 0) return null;
  const by = new Map<string, number>();
  for (const entry of payload) {
    if (typeof entry.value === "number") by.set(String(entry.dataKey), entry.value);
  }
  const total = by.get("total");

  return (
    <div className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[11px] shadow-lg">
      <p className="mb-0.5 font-mono text-[10px] text-[var(--color-text-muted)]">
        {typeof label === "number" ? formatDateTime(label) : ""}
      </p>
      {showTotal && total !== undefined && (
        <p className={`font-mono font-semibold ${pnlTextClass(total)}`}>
          Total {format(total)}
        </p>
      )}
      {keys.map((key) => {
        const value = by.get(ownerDataKey(key));
        if (value === undefined) return null;
        return (
          <p key={key} className="font-mono">
            <span className="text-[var(--color-text-muted)]">
              {labels.get(key) ?? key}
            </span>{" "}
            {format(value)}
          </p>
        );
      })}
    </div>
  );
}

/** The windows the chips offer, and what each one means as a `TimeRange`. */
const WINDOWS = [
  { value: "1d", label: "1D", ms: 24 * 3_600_000 },
  { value: "7d", label: "7D", ms: 7 * 24 * 3_600_000 },
  { value: "30d", label: "30D", ms: 30 * 24 * 3_600_000 },
  { value: "all", label: "All", ms: 0 },
] as const;

type WindowValue = (typeof WINDOWS)[number]["value"];

/**
 * `?range=` → a window.
 *
 * Falls back to its default rather than throwing — `parsePopulation`'s stated
 * rule: a stale or hand-edited parameter should land the reader on the page
 * they asked for.
 */
function parseWindow(raw: string | null): {
  value: WindowValue;
  range: TimeRange | null;
} {
  const found = WINDOWS.find((w) => w.value === raw);
  if (!found || found.ms === 0) return { value: "all", range: null };
  return { value: found.value, range: { start: null, end: null, trailing: found.ms } };
}
