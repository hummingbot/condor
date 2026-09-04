// ── The PNL evolution chart: header strip + PNL pane + volume/position pane ──
//
// One component draws every PNL evolution chart in the app (ARCH-242). Before
// this, the bots page and the single-controller chart each carried their own
// byte-for-byte copy of the same ~100 lines of recharts JSX, and the two had
// already drifted apart — the controller header never showed the Pos stat its
// own bottom pane was drawing.
//
// Its two callers now own only what is genuinely theirs: where the points come
// from — PerfBrowser folds the selected scope's snapshots with
// aggregatePnlSeries and passes the series straight in; ControllerPnlChart runs
// one controller's history query. Everything you can see — the header, the
// gradient, the axes, the tooltips, the legend, the series — lives here, once.
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
//     instance from useId(), not hardcoded. This one is defensive: today the
//     only mount site is PerfBrowser's ternary, which puts either
//     ControllerPnlChart or this component on the page, never both — so no
//     second instance exists to collide with. It is written this way because a
//     file-global id fails silently rather than loudly: the moment anything
//     mounts a second chart, the second instance's area would resolve to the
//     first instance's gradient and the two would cross-sync their tooltips,
//     with nothing in the types or the tests to say why. Deriving the id per
//     instance costs a hook and removes the trap.

import { useCallback, useId, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
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
  PANE_LABELS,
  PNL_SERIES_COLORS,
  PNL_SERIES_LABELS,
  PNL_SERIES_PANE,
  RANGE_PRESETS,
  chartBucketMs,
  formatBucketLabel,
  hiddenSeriesSnapshot,
  paneSeries,
  positionAreaExtent,
  positionAxisDomain,
  resolveTimeRange,
  setSeriesHidden,
  sliceToRange,
  subscribeToHiddenSeries,
  volumeBarWidth,
  zeroGradientOffset,
  type PnlChartPoint,
  type PnlSeriesKey,
  type TimeRange,
} from "@/lib/pnl-chart";
import { getThemeColors } from "@/lib/theme-colors";
import { PnlEvolutionTooltip } from "./PnlChartTooltips";
import { PnlRangeStrip } from "./PnlRangeStrip";

/**
 * The hover card floats over whichever pane it escaped into, so it needs to
 * paint above the other pane's SVG rather than under it.
 */
const TOOLTIP_WRAPPER_STYLE = { zIndex: 10 } as const;

/**
 * Let the card leave the activity pane through its top edge — see the tooltip
 * in that pane. `allowEscapeViewBox` lifts the clamp; `reverseDirection` is
 * what makes the released direction *up* instead of down.
 */
const ESCAPE_UPWARD = { x: false, y: true } as const;

/** Both panes pad their chart identically; the divider's inset counts this in. */
const PANE_PADDING = { paddingLeft: PANE_PAD_X, paddingRight: PANE_PAD_X } as const;

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
 * Whether a legend entry is a control, and what it currently controls
 * (FEAT-085).
 *
 * `locked` is the last series left drawn in its pane: switching it off would
 * leave a pane with no marks, and the only thing that could put them back is
 * the entry that emptied it — a control that cannot undo itself. Marked
 * `aria-disabled` rather than `disabled` so it keeps its tooltip and stays
 * reachable by the keyboard, which is where the explanation is.
 */
interface LegendToggle {
  drawn: boolean;
  locked: boolean;
  onToggle: () => void;
}

/**
 * One legend row: the mark, the name, and the series' live value.
 *
 * `hint` qualifies the *name* (what one bar covers) and `suffix` qualifies the
 * *value* (over what window it was measured) — the two halves of keeping the
 * volume flow apart from the volume stock. A row with no swatch is a number the
 * chart does not draw.
 *
 * ── The row is also the switch (FEAT-085) ──
 *
 * Given a `toggle`, the row is a button that draws its series or does not. The
 * legend is the control rather than a second row of chips above the chart,
 * because every other placement would have to name the five series again —
 * and PNL_SERIES_LABELS exists precisely so that nothing on screen names them
 * twice (READ-244). It is also where the eye already is when deciding a line is
 * noise.
 *
 * Switched off, the row dims and its swatch is struck through, but it is never
 * removed: a control that vanishes when used cannot be used to undo itself. Its
 * *value* does go, and that is the point rather than an omission — a live
 * reading of a series the chart is not drawing is a number with no mark to
 * attach it to, which is the same complaint the legend was built to answer.
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
  toggle,
}: {
  series: string;
  swatch?: ReactNode;
  name: string;
  hint?: string;
  value: string;
  color: string;
  suffix?: string;
  strong?: boolean;
  toggle?: LegendToggle;
}) {
  const off = toggle ? !toggle.drawn : false;
  const body = (
    <>
      <span className="relative inline-flex shrink-0">
        {swatch ?? <Swatch />}
        {off && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 top-1/2 h-px -rotate-12 bg-[var(--color-text-muted)]"
          />
        )}
      </span>
      <span className="text-[var(--color-text-muted)]">
        {name}
        {hint && <span className="opacity-60"> {hint}</span>}
      </span>
      {!off && (
        <>
          <span className={strong ? "font-semibold" : undefined} style={{ color }}>
            {value}
          </span>
          {suffix && <span className="text-[var(--color-text-muted)] opacity-60">{suffix}</span>}
        </>
      )}
    </>
  );

  if (!toggle) {
    return (
      <span className="flex items-center gap-1.5 whitespace-nowrap" data-legend-entry={series}>
        {body}
      </span>
    );
  }

  return (
    <button
      type="button"
      data-legend-entry={series}
      aria-pressed={toggle.drawn}
      aria-disabled={toggle.locked || undefined}
      title={
        toggle.locked
          ? `${name} is the only series this pane still draws`
          : toggle.drawn
            ? `Hide ${name}`
            : `Show ${name}`
      }
      onClick={toggle.locked ? undefined : toggle.onToggle}
      className={`-mx-1 flex items-center gap-1.5 whitespace-nowrap rounded px-1 transition-colors ${
        toggle.locked ? "cursor-default" : "hover:bg-[var(--color-surface-hover)]"
      } ${off ? "opacity-45" : ""}`}
    >
      {body}
    </button>
  );
}

/**
 * One zoom level, styled as the card's other small toggles are — the card
 * already has a vocabulary for "a small toggle above the panes".
 */
function RangeChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      data-range-preset={label}
      aria-pressed={active}
      onClick={onClick}
      className={`rounded-full px-2 py-0.5 text-[10px] font-medium tabular-nums transition-colors ${
        active
          ? "bg-[var(--color-text-muted)]/20 text-[var(--color-text)]"
          : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      }`}
    >
      {label}
    </button>
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
  /** Optional row between the header and the panes. No caller passes one today. */
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

  // ── Zooming the loaded window (READ-249) ──
  //
  // The card draws whatever slice of `data` the user has selected, and the
  // selection is two *timestamps* rather than two point indices. Both of those
  // are load-bearing and both are explained at `TimeRange` in lib/pnl-chart:
  // this series is rebuilt by a socket every few seconds, so an index means a
  // different instant almost immediately — and recharts' own `<Brush>`, which
  // this item originally proposed, stores exactly those indices and resets them
  // to the full range on every change of the `data` prop's identity.
  //
  // Slicing here instead of asking recharts to narrow anything is also what
  // makes the rest of this file untouched by the feature: both panes are handed
  // the same array, so their X domains, their Y domains, the bar geometry, the
  // synced cursor and the tooltip indices all narrow together, by construction,
  // with no second source of truth to drift from the first.
  const [range, setRange] = useState<TimeRange | null>(null);
  const [viewStart, viewEnd] = resolveTimeRange(data, range);
  const visible = useMemo(() => sliceToRange(data, viewStart, viewEnd), [data, viewStart, viewEnd]);
  const fullSpanMs = data.length > 1 ? data[data.length - 1].time - data[0].time : 0;

  const selectRange = useCallback(
    (start: number, end: number, atLiveEdge: boolean) => {
      // Dragged back out to both ends: that is not a zoom, it is the default,
      // and storing it as one would leave "All" reading as unselected.
      if (atLiveEdge && start <= (data[0]?.time ?? 0)) return setRange(null);
      // A window touching the live edge keeps its width and slides with the
      // points arriving behind it; one that does not is frozen where it was put.
      setRange(atLiveEdge ? { start: null, end: null, trailing: end - start } : { start, end });
    },
    [data],
  );

  // A drag is not a hover. The strip lives outside both pane wrappers, so a
  // traveller pulled up across a pane would fire that pane's `onMouseEnter` and
  // pop the hover card over the very window being resized (and blank it again
  // on the way out). The card stays out of the whole gesture instead.
  const [scrubbing, setScrubbing] = useState(false);

  // recharts compares `tickFormatter` by identity, so these stay stable per symbol.
  const fmtAxis = useCallback((v: number) => formatAxisCurrency(v, currencySymbol, "pnl"), [currencySymbol]);
  const fmtVolAxis = useCallback((v: number) => formatAxisCurrency(v, currencySymbol, "volume"), [currencySymbol]);

  // How much time the axis is actually showing decides how a tick is written:
  // `HH:MM` alone repeats itself across every day of a multi-day window
  // (READ-250). The series is sorted, so its ends are its extremes; both panes
  // share the one formatter so their columns stay labelled alike.
  const spanMs = visible.length > 1 ? visible[visible.length - 1].time - visible[0].time : 0;
  const fmtTimeAxis = useCallback((v: number) => formatAxisTime(v, spanMs), [spanMs]);

  // The right-hand end of what is drawn: the live "now" point appended by
  // aggregatePnlSeries when the window is following it, and the last point of
  // the selection when it is not. The header's per-series values read from
  // here, so they describe the series where the reader can see them end rather
  // than reporting a live figure over a window that stops in the past.
  const latest = visible.length > 0 ? visible[visible.length - 1] : null;
  // Whether this chart draws a position series at all is a property of the
  // whole loaded window, not of the slice on screen: deriving it from the slice
  // would make the position axis' ticks, the area and its legend entry appear
  // and vanish as the user drags across a flat stretch.
  const hasPosition = data.some((p) => p.position !== 0);

  // ── What each pane is left drawing (FEAT-085) ──
  //
  // Two independent says in it: `hasPosition` is a property of the series, and
  // `hidden` is the reader's choice, shared by every chart on this device (see
  // the store in lib/pnl-chart).
  //
  // Everything downstream reads these two arrays rather than testing `hidden`
  // again — the marks, the axis ticks, the tooltip rows, the header's values,
  // the panes' own heights. That is not tidiness: a recharts Y domain is built
  // from the graphical items actually rendered onto it, so dropping a mark
  // while leaving its axis labelled gives a pane scaled to a series nobody can
  // see, and an axis whose every item is gone has no domain left to compute at
  // all. One list decides both, or the two drift.
  const hidden = useSyncExternalStore(subscribeToHiddenSeries, hiddenSeriesSnapshot, hiddenSeriesSnapshot);
  const drawnPnl = useMemo(() => paneSeries("pnl").filter((key) => !hidden.has(key)), [hidden]);
  const drawnActivity = useMemo(
    () => paneSeries("activity").filter((key) => !hidden.has(key) && (key !== "position" || hasPosition)),
    [hidden, hasPosition],
  );
  const drawn = useMemo(
    () => new Set<PnlSeriesKey>([...drawnPnl, ...drawnActivity]),
    [drawnPnl, drawnActivity],
  );
  // A pane with nothing left in it is not drawn at all: an empty grid between
  // two axes scaled to nothing reads as a chart that has broken, not as one
  // that has been switched off.
  const showPnlPane = drawnPnl.length > 0;
  const showActivityPane = drawnActivity.length > 0;

  const legendToggle = useCallback(
    (key: PnlSeriesKey): LegendToggle => {
      const pane = PNL_SERIES_PANE[key] === "pnl" ? drawnPnl : drawnActivity;
      const isDrawn = pane.includes(key);
      return {
        drawn: isDrawn,
        locked: isDrawn && pane.length <= 1,
        onToggle: () => setSeriesHidden(key, isDrawn),
      };
    },
    [drawnPnl, drawnActivity],
  );

  // The position axis is pinned across zero rather than left to recharts, so
  // the signed area always has its baseline on screen (READ-246). Memoised
  // because recharts keeps the domain in its own store and a fresh array on
  // every render would churn it.
  const positionDomain = useMemo(() => positionAxisDomain(visible), [visible]);
  // Measured against the area's own extent, not the padded domain: the fill's
  // gradient is in objectBoundingBox units. See zeroGradientOffset.
  const positionZeroOffset = useMemo(() => zeroGradientOffset(positionAreaExtent(visible)), [visible]);

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
  const bucketMs = useMemo(() => chartBucketMs(visible), [visible]);
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
    () => visible.reduce((sum, p) => (Number.isFinite(p.volumeDelta) ? sum + p.volumeDelta : sum), 0),
    [visible],
  );

  // ── One hover card for both panes (READ-248) ──
  //
  // The panes share a `syncId`, which propagates the active index *and* the
  // active flag: hovering either one activates the tooltip in both. Two
  // <Tooltip>s with two different contents therefore popped two cards at once,
  // each repeating the same timestamp, and the reader had to join them.
  //
  // There is now one card, and it is mounted on both panes rather than on one,
  // because a synced chart's tooltip is placed by projecting the source
  // coordinate proportionally into the receiving chart's viewBox
  // (useChartSynchronisation). Mounted only on the PnL pane, a hover down in
  // the activity pane would draw the card somewhere up in the PnL pane — near
  // the right column, nowhere near the cursor. So both panes carry it and the
  // one under the pointer draws it; the other returns null, which leaves the
  // synced cursor line spanning both panes exactly as before.
  //
  // Which pane that is comes from the pane wrappers rather than from recharts,
  // whose "am I the source or the receiver of a sync" state is internal. The
  // leave handler only clears its own pane, so the leave/enter pair fired when
  // the pointer crosses from one pane to the other cannot land in that order
  // and blank the card.
  const [hoveredPane, setHoveredPane] = useState<keyof typeof PANE_LABELS | null>(null);
  const enterPnlPane = useCallback(() => setHoveredPane("pnl"), []);
  const leavePnlPane = useCallback(() => setHoveredPane((p) => (p === "pnl" ? null : p)), []);
  const enterActivityPane = useCallback(() => setHoveredPane("activity"), []);
  const leaveActivityPane = useCallback(() => setHoveredPane((p) => (p === "activity" ? null : p)), []);

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
        {/* `min-w-0` so this block may shrink past its longest entry and wrap
            instead: a flex item's automatic minimum is its min-content width,
            which in the narrow controller modal pushed the zoom chips past the
            card's `overflow-hidden` edge and clipped the last one. */}
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-[11px] tabular-nums">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            {title}
          </p>
          {latest && (
            <>
              <LegendGroup label={PANE_LABELS.pnl}>
                <LegendEntry
                  series="total"
                  name={PNL_SERIES_LABELS.total}
                  toggle={legendToggle("total")}
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
                  toggle={legendToggle("realized")}
                  value={fmtPnl(latest.realized)}
                  color={tc.up}
                  swatch={<StrokeSwatch color={tc.up} />}
                />
                <LegendEntry
                  series="unrealized"
                  name={PNL_SERIES_LABELS.unrealized}
                  toggle={legendToggle("unrealized")}
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
                  toggle={legendToggle("volumeDelta")}
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
                    toggle={legendToggle("position")}
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
        <div className="flex shrink-0 items-center gap-2">
          {/* One-click zoom levels, beside the stats rather than in a pane's
              caption: they set the window both panes draw, so they belong to
              the card, and a control sitting above one pane would read as that
              pane's. Only the levels shorter than what is loaded are offered —
              "1d" over an eight-hour history is a button that does nothing. */}
          {fullSpanMs > 0 && (
            <div className="flex items-center gap-0.5" data-range-presets>
              {RANGE_PRESETS.filter((preset) => preset.ms < fullSpanMs).map((preset) => (
                <RangeChip
                  key={preset.label}
                  label={preset.label}
                  active={range?.trailing === preset.ms}
                  onClick={() => setRange({ start: null, end: null, trailing: preset.ms })}
                />
              ))}
              <RangeChip label="All" active={range === null} onClick={() => setRange(null)} />
            </div>
          )}
          {notice && (
            <span
              title={notice.detail}
              className="rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide bg-[var(--color-yellow)]/15 text-[var(--color-yellow)] cursor-help"
            >
              {notice.label}
            </span>
          )}
        </div>
      </div>

      {filters}

      {/* PnL pane (top), drawn while it still has a series in it (FEAT-085) */}
      {showPnlPane && (
      <>
      <PaneCaption label={PANE_LABELS.pnl} />
      <div data-pane="pnl" style={PANE_PADDING} onMouseEnter={enterPnlPane} onMouseLeave={leavePnlPane}>
        <ResponsiveContainer width="100%" height={pnlHeight}>
          <ComposedChart data={visible} margin={{ top: 12, right: PANE_MARGIN_RIGHT, left: 0, bottom: 0 }} syncId={instanceId}>
            {drawn.has("total") && (
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={totalColor} stopOpacity={0.15} />
                  <stop offset="95%" stopColor={totalColor} stopOpacity={0.02} />
                </linearGradient>
              </defs>
            )}
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
            {/* The card, drawn here only while the pointer is in this pane.
                Left to recharts' default placement: this pane is tall enough
                that the card fits beside the cursor, and the default flips it
                to the other side of the cursor near an edge rather than over
                the point being read. */}
            <Tooltip
              content={
                <PnlEvolutionTooltip
                  symbol={currencySymbol}
                  bucket={bucketLabel}
                  drawn={drawn}
                  visible={!scrubbing && hoveredPane === "pnl"}
                />
              }
              wrapperStyle={TOOLTIP_WRAPPER_STYLE}
            />
            {drawn.has("total") && (
              <Area type="monotone" dataKey="total" stroke="none" fill={`url(#${gradientId})`} activeDot={false} />
            )}
            {drawn.has("total") && (
              <Line type="monotone" dataKey="total" stroke={totalColor} strokeWidth={2} dot={false} strokeOpacity={0.6} />
            )}
            {drawn.has("realized") && (
              <Line type="monotone" dataKey="realized" stroke={tc.up} strokeWidth={2} dot={false} />
            )}
            {drawn.has("unrealized") && (
              <Line type="monotone" dataKey="unrealized" stroke={PNL_SERIES_COLORS.unrealized} strokeWidth={2} strokeDasharray="5 3" dot={false} />
            )}
            {/* No <Legend> here, in either pane. It could only ever name this
                pane's three series — it printed capitalised dataKeys as one
                little line apiece, a row below the "PnL" caption and inside the
                plot area — while the two series below stayed anonymous. The
                header legend names all five (READ-244). */}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      </>
      )}

      {/* Volume + Position pane (bottom), ruled off from the one above — and
          only ruled off while there *is* one above. */}
      {showActivityPane && (
      <>
      <PaneCaption label={PANE_LABELS.activity} divider={showPnlPane} />
      <div data-pane="activity" style={PANE_PADDING} onMouseEnter={enterActivityPane} onMouseLeave={leaveActivityPane}>
        <ResponsiveContainer width="100%" height={volumeHeight} onResize={onActivityResize}>
          <ComposedChart data={visible} margin={{ top: 4, right: PANE_MARGIN_RIGHT, left: 0, bottom: 4 }} syncId={instanceId}>
            {/* One fill, two sides: a hard stop exactly where the position axis
                crosses zero, so the part of the area above the baseline is
                long-coloured and the part below is short-coloured. The two
                colours are the app's own side colours (see sideColor in
                lib/theme-colors), so they follow the theme — the colourblind
                palette included. */}
            {drawn.has("position") && (
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
              // Silenced along with its bars: with no item left on it, this
              // axis has no domain to compute and its ticks would be labelling
              // an interval nothing was measured over.
              tick={drawn.has("volumeDelta") ? { fontSize: 10, fill: PNL_SERIES_COLORS.volume } : false}
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
              tick={drawn.has("position") ? { fontSize: 10, fill: PNL_SERIES_COLORS.position } : false}
              stroke="var(--color-border)"
              tickLine={false}
              axisLine={false}
              width={AXIS_WIDTH}
            />
            {/* The same card, drawn here instead while the pointer is in this
                pane. It is anchored *above* the cursor: this pane is a fraction
                of the height of the one above it and a card listing five series
                is routinely taller than it, so recharts' default — clamp the
                card inside the pane — would pin it to the pane's top edge and
                let it spill out through the card's `overflow-hidden` bottom.
                Escaping upward puts it over the tall PnL pane, where there is
                always room, and never over the bar it is describing. */}
            <Tooltip
              content={
                <PnlEvolutionTooltip
                  symbol={currencySymbol}
                  bucket={bucketLabel}
                  drawn={drawn}
                  visible={!scrubbing && hoveredPane === "activity"}
                />
              }
              allowEscapeViewBox={ESCAPE_UPWARD}
              reverseDirection={ESCAPE_UPWARD}
              wrapperStyle={TOOLTIP_WRAPPER_STYLE}
            />
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
            {drawn.has("volumeDelta") && (
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
            )}
            {/* Net position: an area filled from zero, not a bare line. The
                stroke keeps the series' own violet — the colour the right-hand
                ticks, the header's Pos stat and the tooltip already use — so
                the fill is free to carry the *sign*. baseValue is pinned to 0
                rather than left to recharts, which otherwise bases an area at
                the domain edge whenever the domain does not straddle zero. */}
            {drawn.has("position") && (
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
            {drawn.has("position") && (
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
      </>
      )}

      {/* The range strip, below the shared time axis it operates on and inset to
          the same plot area, so a column on it is the column above it. */}
      <PnlRangeStrip
        data={data}
        start={viewStart}
        end={viewEnd}
        color={totalColor}
        onSelect={selectRange}
        onScrub={setScrubbing}
      />
    </div>
  );
}
