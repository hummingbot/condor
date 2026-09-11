// ── The range strip: drag to zoom the loaded window (READ-249) ──
//
// A slim track under the two panes showing the *whole* loaded window, with the
// slice currently drawn highlighted inside it and a traveller on each end. Drag
// an end to zoom, drag the middle to pan, drag on empty track to sweep out a
// new window from scratch.
//
// It replaces the recharts `<Brush>` the backlog item proposed, for one reason
// found in recharts 3.8.1 rather than assumed: recharts keeps a brush as two
// indices into the chart's `data` array, and `ChartDataContextProvider` resets
// them to the full range on every change of that array's identity — its effect
// cleanup dispatches `setChartData(undefined)`, zeroing both, and the re-run
// puts the end back at `length - 1`. Nothing re-asserts a *controlled*
// `startIndex`/`endIndex` afterwards either: `BrushInternal`'s effect depends on
// those prop values, and a window anchored in the past has the same index
// values before and after a point is appended, so it never re-fires. On a chart
// whose series is rebuilt by a socket every few seconds, that is a brush which
// silently opens itself on a timer.
//
// So the selection lives here as two timestamps (see `TimeRange`), the panes
// are handed the slice they describe, and recharts is left drawing what it is
// given — no chart state to fight, and the same code path whether the data is
// static, streaming, or refetched at a different sampling interval.
//
// The geometry is the card's own: the track is inset to exactly the plot area
// both panes derive from AXIS_WIDTH, so a column on the strip is the column
// above it. It is sized in percentages of that inset — the SVG uses a 0..100
// viewBox with `preserveAspectRatio="none"` — so it needs no measurement to
// *draw*, only to read a pointer, which it does from the live bounding box.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PLOT_INSET_LEFT, PLOT_INSET_RIGHT, type PnlChartPoint } from "@/lib/pnl-chart";

/** Height of the track itself, px. The row adds its own padding and rule. */
export const RANGE_STRIP_HEIGHT = 24;

/**
 * How close to the right end counts as *on* it.
 *
 * Dragging to the live edge is what asks the window to keep following new
 * points, and the difference between "the last pixel" and "the second to last"
 * is not something a hand can express. A window that stops one pixel short and
 * silently freezes would look identical to one that follows, until it fell
 * behind.
 */
const LIVE_EDGE_SNAP = 0.01;

/** The narrowest window a drag may produce, as a fraction of the loaded span. */
const MIN_SPAN = 0.01;

const TRAVELLER_W = 9;

type Grab = "start" | "end" | "pan";

interface Drag {
  grab: Grab;
  /** The end *not* being moved, held fixed for the duration of the drag. */
  anchor: number;
  /** For a pan: the window's width, and where inside it the pointer took hold. */
  width: number;
  offset: number;
}

interface Props {
  /** The whole loaded window — the strip's own domain, never the zoomed slice. */
  data: PnlChartPoint[];
  /** The selection currently drawn, resolved to absolute instants. */
  start: number;
  end: number;
  /** The total series' colour, so the strip's shape reads as the chart's shape. */
  color: string;
  /**
   * A new selection. `atLiveEdge` says the right end landed on the newest point,
   * which is what the caller stores as "keep following".
   */
  onSelect: (start: number, end: number, atLiveEdge: boolean) => void;
  /** Held true for the length of a drag, so the hover card can stay out of it. */
  onScrub: (scrubbing: boolean) => void;
}

export function PnlRangeStrip({ data, start, end, color, onSelect, onScrub }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const [dragging, setDragging] = useState(false);

  const first = data.length > 0 ? data[0].time : 0;
  const last = data.length > 0 ? data[data.length - 1].time : 0;
  const span = last - first;

  /**
   * The `total` series drawn across the full window, in 0..100 x 0..100 units.
   *
   * Points whose total is not a finite number are skipped, and the pen lifts
   * over them rather than drawing a straight line across the gap — which is
   * both what the panes above do (recharts breaks its curve at a bad value) and
   * a hard requirement: a single NaN reaching the `d` attribute makes the
   * browser reject the *whole path*, so one absent snapshot would blank the
   * strip and log an SVG error on every socket frame.
   */
  const path = useMemo(() => {
    if (data.length < 2 || span <= 0) return "";
    let lo = Infinity;
    let hi = -Infinity;
    for (const point of data) {
      if (!Number.isFinite(point.total)) continue;
      if (point.total < lo) lo = point.total;
      if (point.total > hi) hi = point.total;
    }
    if (!Number.isFinite(lo)) return "";
    const height = hi - lo || 1;
    let d = "";
    let pen = "M";
    for (const point of data) {
      if (!Number.isFinite(point.total)) {
        pen = "M";
        continue;
      }
      const x = ((point.time - first) / span) * 100;
      // 4% of padding top and bottom so a flat series is still a line and a
      // peak is not clipped by the track's edge.
      const y = 96 - ((point.total - lo) / height) * 92;
      d += `${pen}${x.toFixed(3)},${y.toFixed(3)}`;
      pen = "L";
    }
    return d;
  }, [data, first, span]);

  /** Where an instant sits on the track, as a percentage of its width. */
  const pct = useCallback((t: number) => (span > 0 ? ((t - first) / span) * 100 : 0), [first, span]);

  /** The instant under a pointer, clamped to the loaded window. */
  const timeAt = useCallback(
    (clientX: number) => {
      const rect = trackRef.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0) return first;
      const fraction = (clientX - rect.left) / rect.width;
      return Math.min(Math.max(first + fraction * span, first), last);
    },
    [first, last, span],
  );

  const emit = useCallback(
    (nextStart: number, nextEnd: number) => {
      // Snapping is what lets a drag ask to keep following the live edge; see
      // LIVE_EDGE_SNAP.
      const atLiveEdge = nextEnd >= last - span * LIVE_EDGE_SNAP;
      onSelect(nextStart, atLiveEdge ? last : nextEnd, atLiveEdge);
    },
    [last, onSelect, span],
  );

  const beginDrag = useCallback(
    (event: React.MouseEvent, grab: Grab) => {
      if (span <= 0) return;
      event.preventDefault();
      event.stopPropagation();
      const at = timeAt(event.clientX);
      dragRef.current =
        grab === "pan"
          ? { grab, anchor: 0, width: end - start, offset: at - start }
          // Either traveller drag is the same gesture with the *other* end
          // pinned, so both reduce to one sweep against an anchor below.
          : { grab, anchor: grab === "start" ? end : start, width: 0, offset: 0 };
      setDragging(true);
      onScrub(true);
    },
    [end, onScrub, span, start, timeAt],
  );

  /** An empty-track press: anchor here and sweep. */
  const beginSweep = useCallback(
    (event: React.MouseEvent) => {
      if (span <= 0) return;
      event.preventDefault();
      const at = timeAt(event.clientX);
      dragRef.current = { grab: "end", anchor: at, width: 0, offset: 0 };
      setDragging(true);
      onScrub(true);
    },
    [onScrub, span, timeAt],
  );

  useEffect(() => {
    if (!dragging) return;
    const minSpan = span * MIN_SPAN;

    const onMove = (event: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const at = timeAt(event.clientX);
      if (drag.grab === "pan") {
        const nextStart = Math.min(Math.max(at - drag.offset, first), last - drag.width);
        emit(nextStart, nextStart + drag.width);
        return;
      }
      // A sweep that crosses its own anchor is still a window: the two ends
      // just swap, which is what a hand dragging left through the anchor means.
      const lo = Math.min(at, drag.anchor);
      const hi = Math.max(at, drag.anchor);
      if (hi - lo < minSpan) {
        // Too narrow to draw: hold the anchored end still and park the moving
        // one a minimum window away, on the side the hand is on.
        if (at < drag.anchor) emit(Math.max(first, drag.anchor - minSpan), drag.anchor);
        else emit(drag.anchor, Math.min(last, drag.anchor + minSpan));
        return;
      }
      emit(lo, hi);
    };

    const onUp = () => {
      dragRef.current = null;
      setDragging(false);
      onScrub(false);
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    // A drag over a chart would otherwise select the labels it passes over.
    const previousSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = previousSelect;
    };
  }, [dragging, emit, first, last, onScrub, span, timeAt]);

  if (data.length < 3 || span <= 0) return null;

  const left = pct(start);
  const right = pct(end);

  return (
    <div
      className="border-t border-[var(--color-border)] pt-1.5 pb-2"
      style={{ marginLeft: PLOT_INSET_LEFT, marginRight: PLOT_INSET_RIGHT }}
    >
      <div
        ref={trackRef}
        data-range-track
        onMouseDown={beginSweep}
        className="relative cursor-crosshair rounded-sm bg-[var(--color-bg)] ring-1 ring-inset ring-[var(--color-border)]"
        style={{ height: RANGE_STRIP_HEIGHT }}
      >
        <svg
          className="absolute inset-0 h-full w-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <path d={path} fill="none" stroke={color} strokeOpacity={0.55} strokeWidth={1} vectorEffect="non-scaling-stroke" />
        </svg>

        {/* The part of the window not on screen, dimmed rather than hidden: the
            point of the strip is to keep the whole loaded window visible while
            only a slice of it is drawn above. */}
        <div
          className="absolute inset-y-0 left-0 bg-[var(--color-surface)]/70"
          style={{ width: `${left}%` }}
        />
        <div
          className="absolute inset-y-0 right-0 bg-[var(--color-surface)]/70"
          style={{ width: `${100 - right}%` }}
        />

        <div
          data-range-selection
          onMouseDown={(event) => beginDrag(event, "pan")}
          className={`absolute inset-y-0 border-x border-[var(--color-accent)] bg-[var(--color-accent)]/10 ${dragging ? "cursor-grabbing" : "cursor-grab"}`}
          style={{ left: `${left}%`, width: `${Math.max(right - left, 0)}%` }}
        />

        {(
          [
            ["start", left],
            ["end", right],
          ] as const
        ).map(([grab, at]) => (
          <div
            key={grab}
            data-range-traveller={grab}
            role="slider"
            tabIndex={-1}
            aria-label={grab === "start" ? "Window start" : "Window end"}
            aria-valuenow={Math.round(at)}
            aria-valuemin={0}
            aria-valuemax={100}
            onMouseDown={(event) => beginDrag(event, grab)}
            className="absolute inset-y-0 cursor-ew-resize rounded-sm bg-[var(--color-accent)]"
            style={{ left: `${at}%`, width: TRAVELLER_W, marginLeft: -TRAVELLER_W / 2 }}
          >
            <span className="absolute inset-y-1 left-1/2 w-px -translate-x-1/2 bg-[var(--color-bg)] opacity-70" />
          </div>
        ))}
      </div>
    </div>
  );
}
