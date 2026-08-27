// ── The PNL evolution chart: header strip + PNL pane + volume/position pane ──
//
// One component draws every PNL evolution chart in the app (ARCH-242). Before
// this, AggregatedPnlChart (the bots page) and ControllerPnlChart (the
// controller modal) each carried their own byte-for-byte copy of the same ~100
// lines of recharts JSX, and the two had already drifted apart — the controller
// header never showed the Pos stat its own bottom pane was drawing.
//
// Those two callers now own only what is genuinely theirs: where the points
// come from (a live aggregation over enabled controllers vs. a single
// controller's history query), and what sits in the filter row. Everything you
// can see — the header, the gradient, the axes, the tooltips, the legend, the
// series — lives here, once.
//
// Two invariants this file owns, both of which fail *silently* if broken:
//
//  1. Gutters. The two panes are separate charts tied together only by
//     `syncId`, which syncs the cursor and the tooltip index but never the
//     geometry (see AXIS_WIDTH in lib/pnl-chart.ts). Each pane derives its plot
//     area from the container width minus its own gutters, so a given instant
//     sits in the same column in both panes only while their gutters match.
//     Both panes therefore render AXIS_WIDTH on the left *and* AXIS_WIDTH on
//     the right, unconditionally — the right-hand axis is always there, and
//     only its ticks come and go with the data. Add an axis to one pane and you
//     must add its mirror to the other.
//
//  2. Identity. The gradient element id and the `syncId` are derived per
//     instance from useId(), not hardcoded. ControllerBrowser's modal opens
//     over the still-mounted aggregated chart, so a fixed id would make the
//     second instance's area resolve to the first instance's gradient (and
//     cross-sync the two charts' tooltips) the moment both are on the page.

import { useCallback, useId, useMemo, useState, type ReactNode } from "react";
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
  type BarShapeProps,
} from "recharts";

import { formatAxisCurrency, formatAxisTime, formatCurrencyVolume, formatCurrencyPnl, pnlColor } from "@/lib/formatters";
import {
  AXIS_WIDTH,
  PANE_MARGIN_RIGHT,
  PANE_PAD_X,
  PLOT_INSET_LEFT,
  PLOT_INSET_RIGHT,
  PNL_SERIES_COLORS,
  PNL_SERIES_LABELS,
  chartBucketMs,
  formatBucketLabel,
  positionAreaExtent,
  positionAxisDomain,
  volumeBarWidth,
  zeroGradientOffset,
  type PnlChartPoint,
} from "@/lib/pnl-chart";
import { getThemeColors } from "@/lib/theme-colors";
import { BottomTooltip, PnlTooltip } from "./PnlChartTooltips";

/** Both panes pad their chart identically; the divider's inset counts this in. */
const PANE_PADDING = { paddingLeft: PANE_PAD_X, paddingRight: PANE_PAD_X } as const;

/**
 * What each pane is called — above the pane, and again on the legend group that
 * lists that pane's series (READ-244).
 *
 * The words are shared rather than typed twice because they are the only thing
 * tying the two halves of the header legend to the two panes below it: the
 * reader matches "Activity" in the legend to "Activity" over the lower pane.
 * Let those drift and the grouping stops pointing anywhere.
 */
const PANE_LABELS = { pnl: "PnL", activity: "Activity" } as const;

/**
 * The caption above one pane and, on the lower one, the rule that separates the
 * two (READ-247).
 *
 * Before this the two ComposedCharts were bare sibling divs with nothing
 * between them, and because the upper pane deliberately hides its X axis the
 * boundary fell in dead space — so the card read as one chart whose bottom half
 * inexplicably switched units, rather than as two panes measuring two different
 * things.
 *
 * The rule is inset to the plot area instead of running edge to edge. Both
 * panes reserve AXIS_WIDTH on each side (invariant 1 above), so the columns the
 * user is actually reading start at PLOT_INSET_LEFT and stop at
 * PLOT_INSET_RIGHT. A full-bleed rule would cut across both gutters and look
 * like the seam between two stacked cards; one that begins and ends where the
 * grid does reads as what this is — one chart, two panes, one shared time axis.
 * The inset is derived from the gutter for the same reason the axes are: move
 * the gutter and the rule follows it, rather than drifting a few pixels off the
 * grid it is drawn to trace.
 *
 * The row is a flex with the caption on the left and the right-hand slot left
 * empty. READ-247 left it for a per-pane legend; READ-244 then put the legend
 * in the header instead — one legend for all five series rather than two that
 * would have to agree with each other — so the slot is still free, and the
 * caption's job here is to carry the name that legend's groups point back to.
 */
function PaneCaption({ label, divider = false }: { label: string; divider?: boolean }) {
  return (
    <div
      data-pane-caption={label.toLowerCase()}
      className={`flex items-center justify-between pb-0.5 ${divider ? "border-t border-[var(--color-border)] pt-2" : "pt-1.5"}`}
      style={{ marginLeft: PLOT_INSET_LEFT, marginRight: PLOT_INSET_RIGHT }}
    >
      <span className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        {label}
      </span>
    </div>
  );
}

// ── The legend (READ-244) ──
//
// One legend, in the header strip, for every series in both panes.
//
// There used to be no legend for the lower pane at all: its two series were
// decodable only by matching a stroke colour to a coloured Y-axis tick, and
// nothing on screen ever said the words "volume" or "position". The upper pane
// had a recharts <Legend> — which covered only its own three series, printed
// capitalised dataKeys, and rendered inside the plot area one row below the
// "PnL" caption, so it read as a second caption competing with the first. That
// element is gone; a legend that can only ever describe the pane it lives in is
// what made five series need three vocabularies.
//
// Two things the header can do that a recharts legend cannot, and both are the
// point:
//
//  1. **Group by pane.** The entries come in two groups named exactly as the
//     two panes are (PANE_LABELS), so the header states the separation the user
//     complained was invisible — these three are PnL, these two are Activity —
//     before they have looked at the chart at all.
//  2. **Draw the real mark.** Each swatch is the SVG the series is actually
//     drawn with: a solid stroke, a dashed stroke, a stroke over its gradient,
//     a row of bars, a two-tone area over its dashed baseline. recharts offers
//     a fixed set of `iconType`s and would have given the volume bars and the
//     position area the same little line as the PnL curves — a legend that says
//     "line" for a bar series is worse than none.
//
// The one licence taken with "the swatch is the real mark": fill opacities are
// raised at chip scale (see SWATCH_FILL_OPACITY). The chart's fills are faint
// because they are tints spread over 100+ px of pane; the same alpha over 12 px
// is nothing at all, and an invisible fill would misreport a filled series as a
// bare line. Shape, colour, dash and the two-tone split are exact.

/** One swatch, drawn at the size of a word. */
const SWATCH_W = 20;
const SWATCH_H = 12;
/** A pane's fill spread over 12px would vanish; see the note above. */
const SWATCH_FILL_OPACITY = 0.4;

function Swatch({ children }: { children?: ReactNode }) {
  return (
    <svg
      width={SWATCH_W}
      height={SWATCH_H}
      viewBox={`0 0 ${SWATCH_W} ${SWATCH_H}`}
      aria-hidden="true"
      className="shrink-0"
    >
      {children}
    </svg>
  );
}

/** A stroked series: `dashed` reproduces the Unrealized line's own dash array. */
function StrokeSwatch({ color, dashed = false, opacity = 1 }: { color: string; dashed?: boolean; opacity?: number }) {
  return (
    <Swatch>
      <line
        x1={0}
        y1={SWATCH_H / 2}
        x2={SWATCH_W}
        y2={SWATCH_H / 2}
        stroke={color}
        strokeWidth={2}
        strokeOpacity={opacity}
        strokeDasharray={dashed ? "5 3" : undefined}
      />
    </Swatch>
  );
}

/**
 * One legend row: the mark, the name, and the series' live value.
 *
 * `hint` qualifies the *name* (what one bar covers) and `suffix` qualifies the
 * *value* (over what window it was measured) — the two halves of keeping the
 * volume flow apart from the volume stock. A row with no swatch is a number the
 * chart does not draw.
 */
function LegendEntry({
  series,
  swatch,
  name,
  hint,
  value,
  color,
  suffix,
  strong = false,
}: {
  series: string;
  swatch?: ReactNode;
  name: string;
  hint?: string;
  value: string;
  color: string;
  suffix?: string;
  strong?: boolean;
}) {
  return (
    <span className="flex items-center gap-1.5 whitespace-nowrap" data-legend-entry={series}>
      {swatch ?? <Swatch />}
      <span className="text-[var(--color-text-muted)]">
        {name}
        {hint && <span className="opacity-60"> {hint}</span>}
      </span>
      <span className={strong ? "font-semibold" : undefined} style={{ color }}>
        {value}
      </span>
      {suffix && <span className="text-[var(--color-text-muted)] opacity-60">{suffix}</span>}
    </span>
  );
}

/** The entries belonging to one pane, under that pane's own name. */
function LegendGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-3" data-legend-group={label.toLowerCase()}>
      <span className="text-[9px] font-bold uppercase tracking-widest text-[var(--color-text-muted)] opacity-70">
        {label}
      </span>
      {children}
    </div>
  );
}

interface Props {
  /** The timeline both panes draw. Callers own how it is built and when it is empty. */
  data: PnlChartPoint[];
  /** Header label, e.g. "Portfolio PnL" or "PnL Evolution". */
  title: string;
  /** Height of the PNL pane, px. */
  pnlHeight: number;
  /** Height of the volume/position pane, px. */
  volumeHeight: number;
  /** Currency the values are already expressed in — used for ticks, stats and tooltips. */
  currencySymbol?: string;
  /** Optional row between the header and the panes (the aggregated chart's controller chips). */
  filters?: ReactNode;
  /**
   * A short warning shown beside the header stats.
   *
   * It exists for one case in particular: a history assembled from a bounded
   * cursor walk that hit its cap, or lost a page to a failing server, is missing
   * its *oldest* end — and a truncated series drawn without comment is
   * indistinguishable from a complete one, which is exactly the bug that made
   * this chart show eight hours of a five-day fleet (CORR-237). Whatever
   * shortens a series has to say so here.
   */
  notice?: { label: string; detail?: string };
}

export function PnlEvolutionChart({ data, title, pnlHeight, volumeHeight, currencySymbol = "$", filters, notice }: Props) {
  // Unique per mounted instance: two charts on one page must not share a
  // gradient element or a sync group. useId() emits colons, which are legal in
  // an id but awkward in selectors, so strip them.
  const instanceId = useId().replace(/[^a-zA-Z0-9]/g, "");
  const gradientId = `pnlGrad${instanceId}`;
  const posGradientId = `posGrad${instanceId}`;
  // The legend's two filled swatches carry their own copies of those gradients:
  // an SVG paint server lives in the document, not in the element that declared
  // it, so a chip could reference the pane's gradient — until the pane it
  // borrows from is the one conditional on `hasPosition`, or a second chart
  // mounts. Its own id, derived from the same instance, cannot dangle.
  const totalChipId = `totalChip${instanceId}`;
  const posChipId = `posChip${instanceId}`;

  // recharts compares `tickFormatter` by identity, so these stay stable per symbol.
  const fmtAxis = useCallback((v: number) => formatAxisCurrency(v, currencySymbol, "pnl"), [currencySymbol]);
  const fmtVolAxis = useCallback((v: number) => formatAxisCurrency(v, currencySymbol, "volume"), [currencySymbol]);

  // How much time the axis is actually showing decides how a tick is written:
  // `HH:MM` alone repeats itself across every day of a multi-day window
  // (READ-250). The series is sorted, so its ends are its extremes; both panes
  // share the one formatter so their columns stay labelled alike.
  const spanMs = data.length > 1 ? data[data.length - 1].time - data[0].time : 0;
  const fmtTimeAxis = useCallback((v: number) => formatAxisTime(v, spanMs), [spanMs]);

  // Latest point is the live "now" point appended by aggregatePnlSeries
  const latest = data.length > 0 ? data[data.length - 1] : null;
  const hasPosition = data.some((p) => p.position !== 0);

  // The position axis is pinned across zero rather than left to recharts, so
  // the signed area always has its baseline on screen (READ-246). Memoised
  // because recharts keeps the domain in its own store and a fresh array on
  // every render would churn it.
  const positionDomain = useMemo(() => positionAxisDomain(data), [data]);
  // Measured against the area's own extent, not the padded domain: the fill's
  // gradient is in objectBoundingBox units. See zeroGradientOffset.
  const positionZeroOffset = useMemo(() => zeroGradientOffset(positionAreaExtent(data)), [data]);

  // ── Volume bars (READ-245) ──
  //
  // The bars are sized by us, not by recharts. On a numeric X axis recharts
  // takes a bar's width from the *smallest* gap between two adjacent points and
  // clamps any explicit `barSize` back under it — and this series always has
  // one gap far smaller than the rest, because the fold ends it with a live
  // "now" point a fraction of a bucket after the last snapshot. Left alone,
  // every bar in the pane would be drawn at that fraction, thinning to a
  // hairline and thickening again with each snapshot that lands. See
  // `volumeBarWidth` and `chartBucketMs`.
  //
  // The measurement comes from the pane's own ResponsiveContainer, which is
  // already observing its size, rather than from a second observer of ours. It
  // is 0 until the first callback — and stays 0 where there is no layout at all
  // — which `volumeBarWidth` answers with `undefined`, i.e. "leave it to
  // recharts".
  const [activityWidth, setActivityWidth] = useState(0);
  const onActivityResize = useCallback((width: number) => setActivityWidth(width), []);
  const bucketMs = useMemo(() => chartBucketMs(data), [data]);
  // The bucket has to be named in the tooltip: "Volume" used to be a running
  // total, which needs no qualifier, and is now one bucket's worth, which means
  // nothing until you know how long a bucket is.
  const bucketLabel = useMemo(() => formatBucketLabel(bucketMs), [bucketMs]);
  const barWidth = volumeBarWidth(
    // The plot area, not the card: both gutters and the right margin are
    // outside the time domain the bars are placed in.
    activityWidth - 2 * AXIS_WIDTH - PANE_MARGIN_RIGHT,
    spanMs,
    bucketMs,
  );
  // Centred on its instant rather than starting there (recharts' own
  // convention on a numeric axis), so a bar sits under the synced cursor and
  // the tooltip that reports it, in both panes.
  const volumeBar = useCallback(
    (props: BarShapeProps) => {
      const width = barWidth ?? props.width;
      const x = props.x + props.width / 2 - width / 2;
      return (
        <Rectangle
          x={x}
          y={props.y}
          width={width}
          height={props.height}
          radius={props.radius}
          fill={props.fill}
          fillOpacity={props.fillOpacity}
          stroke="none"
        />
      );
    },
    [barWidth],
  );

  // What the bars on screen add up to — the flow the activity pane draws, as
  // opposed to `latest.volume`, the lifetime counter they were differenced from
  // (READ-245). The legend reports both, side by side and each with its own
  // qualifier, because they are different quantities rather than a
  // disagreement: pan the window and this moves, that does not.
  //
  // Non-finite deltas are skipped rather than summed, because that is what the
  // pane does with them: a snapshot whose `volume_traded` did not arrive as a
  // number folds to a NaN delta, and recharts answers that by drawing no bar.
  // A plain `+` would answer it by turning the whole figure into "$NaN" — this
  // total has to say what the bars beside it add up to, and one absent bar is
  // not grounds for withdrawing the other four hundred.
  const windowVolume = useMemo(
    () => data.reduce((sum, p) => (Number.isFinite(p.volumeDelta) ? sum + p.volumeDelta : sum), 0),
    [data],
  );

  const tc = getThemeColors();
  const totalColor = (latest?.total ?? 0) >= 0 ? tc.up : tc.down;
  const fmtPnl = (v: number) => formatCurrencyPnl(v, currencySymbol);
  const fmtVol = (v: number) => formatCurrencyVolume(v, currencySymbol);

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      {/* Header: the one legend for both panes' five series, with each series'
          live value (READ-244). It wraps rather than truncating — the
          controller modal is narrower than the bots page, and an entry pushed
          off the end would be a series left unnamed again. */}
      <div className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] tabular-nums">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            {title}
          </p>
          {latest && (
            <>
              <LegendGroup label={PANE_LABELS.pnl}>
                <LegendEntry
                  series="total"
                  name={PNL_SERIES_LABELS.total}
                  value={fmtPnl(latest.total)}
                  color={pnlColor(latest.total)}
                  strong
                  swatch={
                    // A stroke at the line's own 0.6 opacity, over the gradient
                    // the area beneath it is filled with.
                    <Swatch>
                      <defs>
                        <linearGradient id={totalChipId} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor={totalColor} stopOpacity={SWATCH_FILL_OPACITY} />
                          <stop offset="95%" stopColor={totalColor} stopOpacity={0.03} />
                        </linearGradient>
                      </defs>
                      <rect x={0} y={3} width={SWATCH_W} height={SWATCH_H - 3} fill={`url(#${totalChipId})`} />
                      <line x1={0} y1={4} x2={SWATCH_W} y2={4} stroke={totalColor} strokeWidth={2} strokeOpacity={0.6} />
                    </Swatch>
                  }
                />
                <LegendEntry
                  series="realized"
                  name={PNL_SERIES_LABELS.realized}
                  value={fmtPnl(latest.realized)}
                  color={tc.up}
                  swatch={<StrokeSwatch color={tc.up} />}
                />
                <LegendEntry
                  series="unrealized"
                  name={PNL_SERIES_LABELS.unrealized}
                  value={fmtPnl(latest.unrealized)}
                  color={PNL_SERIES_COLORS.unrealized}
                  swatch={<StrokeSwatch color={PNL_SERIES_COLORS.unrealized} dashed />}
                />
              </LegendGroup>

              <span aria-hidden="true" className="h-3.5 w-px bg-[var(--color-border)]" />

              <LegendGroup label={PANE_LABELS.activity}>
                <LegendEntry
                  series="volumeDelta"
                  name={PNL_SERIES_LABELS.volumeDelta}
                  // The bucket qualifies the *bars*: without it "$40K" is a busy
                  // hour or a dead day and the reader cannot tell which.
                  hint={bucketLabel ? `${bucketLabel} bars` : "bars"}
                  // ...and the suffix qualifies the *number*: this is what the
                  // bars on screen add up to, which is not the lifetime figure
                  // one entry along.
                  value={fmtVol(windowVolume)}
                  suffix="on screen"
                  color={PNL_SERIES_COLORS.volume}
                  swatch={
                    <Swatch>
                      {[
                        [1, 7],
                        [7.5, 11],
                        [14, 5],
                      ].map(([x, h]) => (
                        <rect
                          key={x}
                          x={x}
                          y={SWATCH_H - h}
                          width={5}
                          height={h}
                          rx={1}
                          fill={PNL_SERIES_COLORS.volume}
                          fillOpacity={SWATCH_FILL_OPACITY}
                        />
                      ))}
                    </Swatch>
                  }
                />
                {hasPosition && (
                  <LegendEntry
                    series="position"
                    name={PNL_SERIES_LABELS.position}
                    value={fmtVol(latest.position)}
                    color={PNL_SERIES_COLORS.position}
                    swatch={
                      // The series' violet stroke crossing its own dashed zero,
                      // with the fill splitting long/short at that baseline —
                      // the mark READ-246 gave it, at chip scale.
                      <Swatch>
                        <defs>
                          <linearGradient id={posChipId} x1="0" y1="0" x2="0" y2="1">
                            <stop offset={0.5} stopColor={tc.up} />
                            <stop offset={0.5} stopColor={tc.down} />
                          </linearGradient>
                        </defs>
                        <path
                          d={`M0,${SWATCH_H / 2} L0,2 L${SWATCH_W / 2},${SWATCH_H / 2} L${SWATCH_W},10 L${SWATCH_W},${SWATCH_H / 2} Z`}
                          fill={`url(#${posChipId})`}
                          fillOpacity={SWATCH_FILL_OPACITY}
                        />
                        <line
                          x1={0}
                          y1={SWATCH_H / 2}
                          x2={SWATCH_W}
                          y2={SWATCH_H / 2}
                          stroke={PNL_SERIES_COLORS.position}
                          strokeOpacity={0.45}
                          strokeDasharray="4 4"
                        />
                        <polyline
                          points={`0,2 ${SWATCH_W / 2},${SWATCH_H / 2} ${SWATCH_W},10`}
                          fill="none"
                          stroke={PNL_SERIES_COLORS.position}
                          strokeWidth={1.5}
                        />
                      </Swatch>
                    }
                  />
                )}
                {/* No swatch, and that is the entry's whole point: the lifetime
                    counter is the one number here that nothing on the chart
                    draws. The bars are the flow, this is the stock; keeping
                    both visible, each with its qualifier, is what stops the
                    "on screen" total from reading as a shrunken lifetime one. */}
                <LegendEntry
                  series="volume"
                  name="Traded"
                  hint="lifetime"
                  value={fmtVol(latest.volume)}
                  color="var(--color-text-muted)"
                />
              </LegendGroup>
            </>
          )}
        </div>
        {notice && (
          <span
            title={notice.detail}
            className="rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide bg-[var(--color-yellow)]/15 text-[var(--color-yellow)] cursor-help"
          >
            {notice.label}
          </span>
        )}
      </div>

      {filters}

      {/* PnL pane (top) */}
      <PaneCaption label={PANE_LABELS.pnl} />
      <div data-pane="pnl" style={PANE_PADDING}>
        <ResponsiveContainer width="100%" height={pnlHeight}>
          <ComposedChart data={data} margin={{ top: 12, right: PANE_MARGIN_RIGHT, left: 0, bottom: 0 }} syncId={instanceId}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={totalColor} stopOpacity={0.15} />
                <stop offset="95%" stopColor={totalColor} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" strokeOpacity={0.5} />
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
            {/* Empty right gutter mirroring the bottom pane's position axis so
                both panes keep identical plot areas — invariant 1 above. */}
            <YAxis
              yAxisId="spacer"
              orientation="right"
              tick={false}
              tickLine={false}
              axisLine={false}
              width={AXIS_WIDTH}
            />
            <ReferenceLine y={0} stroke="var(--color-text-muted)" strokeOpacity={0.3} strokeDasharray="4 4" />
            <Tooltip content={<PnlTooltip symbol={currencySymbol} />} />
            <Area type="monotone" dataKey="total" stroke="none" fill={`url(#${gradientId})`} activeDot={false} />
            <Line type="monotone" dataKey="total" stroke={totalColor} strokeWidth={2} dot={false} strokeOpacity={0.6} />
            <Line type="monotone" dataKey="realized" stroke={tc.up} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="unrealized" stroke={PNL_SERIES_COLORS.unrealized} strokeWidth={2} strokeDasharray="5 3" dot={false} />
            {/* No <Legend> here, in either pane. It could only ever name this
                pane's three series — it printed capitalised dataKeys as one
                little line apiece, a row below the "PnL" caption and inside the
                plot area — while the two series below stayed anonymous. The
                header legend names all five (READ-244). */}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Volume + Position pane (bottom), ruled off from the one above */}
      <PaneCaption label={PANE_LABELS.activity} divider />
      <div data-pane="activity" style={PANE_PADDING}>
        <ResponsiveContainer width="100%" height={volumeHeight} onResize={onActivityResize}>
          <ComposedChart data={data} margin={{ top: 4, right: PANE_MARGIN_RIGHT, left: 0, bottom: 4 }} syncId={instanceId}>
            {/* One fill, two sides: a hard stop exactly where the position axis
                crosses zero, so the part of the area above the baseline is
                long-coloured and the part below is short-coloured. The two
                colours are the app's own side colours (see sideColor in
                lib/theme-colors), so they follow the theme — the colourblind
                palette included. */}
            {hasPosition && (
              <defs>
                <linearGradient id={posGradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset={positionZeroOffset} stopColor={tc.up} />
                  <stop offset={positionZeroOffset} stopColor={tc.down} />
                </linearGradient>
              </defs>
            )}
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" strokeOpacity={0.5} />
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
            {/* Always rendered so the gutters match the pane above; only its
                ticks depend on whether there is a position to label. */}
            <YAxis
              yAxisId="pos"
              orientation="right"
              domain={positionDomain}
              tickFormatter={fmtVolAxis}
              tick={hasPosition ? { fontSize: 10, fill: PNL_SERIES_COLORS.position } : false}
              stroke="var(--color-border)"
              tickLine={false}
              axisLine={false}
              width={AXIS_WIDTH}
            />
            <Tooltip content={<BottomTooltip symbol={currencySymbol} bucket={bucketLabel} />} />
            {/* Trading activity, one bar per sampling bucket (READ-245). The
                cumulative counter it is differenced from stays in the header's
                Vol stat, where a running total belongs; here the question is
                *when* the fleet traded, which a monotone ramp cannot answer.
                Bars grow from the vol axis's own zero — the pane floor — and
                that floor is not the position series' baseline: the violet rule
                below is the only zero worth marking on this pane (READ-246).

                Layered behind the position area, and that takes `zIndex`
                rather than JSX order: recharts groups graphical items by type
                and paints every Bar after every Area, so written in the
                obvious order these rectangles come out *over* the one line
                whose shape this pane exists to show. Behind it they read as
                the background histogram they are, and the area's 0.22 fill —
                left exactly where READ-246 set it — tints them without hiding
                either. The fill opacity here is what keeps them a backdrop
                rather than a wall. */}
            <Bar
              zIndex={-1}
              yAxisId="vol"
              dataKey="volumeDelta"
              fill={PNL_SERIES_COLORS.volume}
              fillOpacity={0.45}
              radius={[2, 2, 0, 0]}
              shape={volumeBar}
              // A wide window is hundreds of rects; recharts would animate
              // every one of them on every refresh, and the socket refreshes
              // this series continuously. The lines above are one path each and
              // can afford it — a histogram cannot.
              isAnimationActive={false}
            />
            {/* Net position: an area filled from zero, not a bare line. The
                stroke keeps the series' own violet — the colour the right-hand
                ticks, the header's Pos stat and the tooltip already use — so
                the fill is free to carry the *sign*. baseValue is pinned to 0
                rather than left to recharts, which otherwise bases an area at
                the domain edge whenever the domain does not straddle zero. */}
            {hasPosition && (
              <Area
                yAxisId="pos"
                type="monotone"
                dataKey="position"
                baseValue={0}
                stroke={PNL_SERIES_COLORS.position}
                strokeWidth={1.5}
                fill={`url(#${posGradientId})`}
                fillOpacity={0.22}
                dot={false}
              />
            )}
            {/* Drawn after the area so the baseline stays visible through the
                fill. It is violet, not neutral: this pane has two Y axes and
                only the right-hand one has a zero worth marking — the volume
                axis starts at zero by construction, at the pane's floor. */}
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
    </div>
  );
}
