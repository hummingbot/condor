// ── The activity pane's wiring, in one place (ARCH-341) ──
//
// `lib/pnl-chart` holds everything pure about the two PNL charts — the axis
// gutter, the pane insets, the bucket and bar geometry, the position axis
// rules. What used to sit outside it, copied into both `PnlEvolutionChart` and
// `OwnerPnlChart`, was the *wiring* that turns those helpers into an activity
// pane: the measured width, the bar shape that re-centres a rect on its
// instant, and the two position-axis memos. Two copies meant the recharts
// workaround below was explained twice and could be fixed once.
//
// It lives beside `pnl-chart.ts` rather than inside it because the bar shape is
// JSX and that module is a plain `.ts` with no React import.

import { useCallback, useMemo, useState, type ReactElement } from "react";
import { Rectangle, type BarShapeProps } from "recharts";

import {
  AXIS_WIDTH,
  PANE_MARGIN_RIGHT,
  chartBucketMs,
  formatBucketLabel,
  positionAreaExtent,
  positionAxisDomain,
  volumeBarWidth,
  zeroGradientOffset,
  type PnlChartPoint,
  type SamplingInterval,
} from "@/lib/pnl-chart";

export interface ActivityPane {
  /** Hand to the pane's `<ResponsiveContainer onResize>`; sizes the bars. */
  onActivityResize: (width: number) => void;
  /** The window's bucket, in ms — one bar's worth of volume. */
  bucketMs: number;
  /** That bucket named for the tooltip and the caption, when it has a name. */
  bucketLabel: SamplingInterval | undefined;
  /** The `<Bar shape>` that draws a bar at our width, centred on its instant. */
  volumeBar: (props: BarShapeProps) => ReactElement;
  /** The position axis' domain, pinned across zero. */
  positionDomain: [number, number];
  /** Where zero falls in the position area's own extent, for its gradient. */
  positionZeroOffset: number;
}

/**
 * Everything the activity pane needs that is not markup, derived from the
 * window it is drawing (READ-245, READ-246).
 *
 * @param visible the points currently on screen
 * @param spanMs  the window's time span, `last.time - first.time`
 */
export function useActivityPane(visible: PnlChartPoint[], spanMs: number): ActivityPane {
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

  // The bars are sized by us, not by recharts. On a numeric X axis recharts
  // takes a bar's width from the *smallest* gap between two adjacent points and
  // clamps any explicit `barSize` back under it — and this series always has
  // one gap far smaller than the rest, because the fold ends it with a live
  // "now" point a fraction of a bucket after the last snapshot. Left alone,
  // every bar in the pane would be drawn at that fraction, thinning to a
  // hairline and thickening again with each snapshot that lands. See
  // `volumeBarWidth` and `chartBucketMs`.
  const barWidth = volumeBarWidth(
    // The plot area, not the card: both gutters and the right margin are
    // outside the time domain the bars are placed in.
    activityWidth - 2 * AXIS_WIDTH - PANE_MARGIN_RIGHT,
    spanMs,
    bucketMs,
  );
  // Centred on its instant rather than starting there (recharts' own
  // convention on a numeric axis), so a bar sits under the synced cursor and
  // the tooltip that reports it, in both panes. Everything cosmetic is
  // forwarded from the `<Bar>`, so a pane sets its own radius and opacity.
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

  // The position axis is pinned across zero rather than left to recharts, so
  // the signed area always has its baseline on screen (READ-246). Memoised
  // because recharts keeps the domain in its own store and a fresh array on
  // every render would churn it.
  const positionDomain = useMemo(() => positionAxisDomain(visible), [visible]);
  // Measured against the area's own extent, not the padded domain: the fill's
  // gradient is in objectBoundingBox units. See zeroGradientOffset.
  const positionZeroOffset = useMemo(
    () => zeroGradientOffset(positionAreaExtent(visible)),
    [visible],
  );

  return {
    onActivityResize,
    bucketMs,
    bucketLabel,
    volumeBar,
    positionDomain,
    positionZeroOffset,
  };
}
