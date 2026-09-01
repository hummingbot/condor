// ── Grab a price line and pull it ──
//
// The gesture that turns a hovered line into a moving one. It does not own a
// listener of its own: it returns handlers the chart pane spreads onto the same
// `onPointerDown` / `onPointerUp` pair the tap/pan gate already uses. A drag is
// exactly the "pointer travelled past the slop" case that gate detects and
// otherwise discards — so this hook shares the gate's threshold rather than
// inventing a second one, and the two split the gesture cleanly between them:
// past the slop it is a drag and `onPointerUp` reports that it consumed the
// release; inside the slop it was a tap and the pane's click branches run
// untouched, so a stationary click on a line still picks.
//
// The write-back is the existing pick channel, `onPriceSet(slot, price)`, fired
// once per animation frame while the pointer moves. Live rather than
// commit-on-release: watching the grid's preview levels redistribute under the
// pointer is the point of dragging a boundary at all.
//
// While a drag runs the chart's own crosshair is taken down. Its price-scale
// label tracks the pointer row, which during a drag is exactly the row the
// dragged line's label occupies — so the two land on top of each other and the
// number you are steering by is hidden behind the number saying where the
// cursor is. The line follows the pointer anyway, so the crosshair has nothing
// left to say for the length of the gesture.

import { useCallback, useEffect, useRef } from "react";

import { roundToPricePrecision } from "@/lib/formatters";
import type { PickSlot } from "@/components/executor/types";
import { hitTargetAt, type DragTarget } from "./priceLineDrag";

type Chart = import("lightweight-charts").IChartApi;
type Series = import("lightweight-charts").ISeriesApi<"Candlestick">;

export interface PriceLineDragParams {
  /** The pane element the chart was created in; pointer coordinates are relative to it. */
  getContainer: () => HTMLElement | null;
  getChart: () => Chart | null;
  getSeries: () => Series | null;
  /** The grabbable lines, read fresh on every hit test so a moving price never restages anything. */
  getTargets: () => readonly DragTarget[];
  getPricePrecision: () => number | undefined;
  onPriceSet: (slot: PickSlot, price: number) => void;
  /** How far the pointer must travel before the press stops being a click. */
  slopPx: number;
}

export interface PriceLineDragHandlers {
  onPointerDown: (e: React.PointerEvent) => void;
  onPointerMove: (e: React.PointerEvent) => void;
  /** @returns true when this release ended a real drag, so no click branch may run on it. */
  onPointerUp: (e: React.PointerEvent) => boolean;
  /** A cancelled pointer drops the gesture where it is — no final commit. */
  onPointerCancel: () => void;
  isDragging: () => boolean;
}

interface Grab {
  slot: PickSlot;
  /** Where the press landed, so the slop is measured from it. */
  originX: number;
  originY: number;
  /** Set once the pointer travelled far enough that this is a drag, not a tap. */
  moved: boolean;
}

export function usePriceLineDrag(params: PriceLineDragParams): PriceLineDragHandlers {
  // The parameters carry callbacks a parent mints fresh every render. Held in a
  // ref and read at call time, so the handlers keep one identity for the life of
  // the pane and a drag in flight always writes through the current callback.
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const grabRef = useRef<Grab | null>(null);
  const pointerIdRef = useRef<number | null>(null);
  const frameRef = useRef<number | null>(null);
  const pendingYRef = useRef<number | null>(null);

  /** Pane-relative pixel row for a pointer event. */
  const rowOf = (clientY: number): number | null => {
    const container = paramsRef.current.getContainer();
    if (!container) return null;
    return clientY - container.getBoundingClientRect().top;
  };

  /** Map one pixel row through the price scale and report it, rounded. */
  const commit = useCallback((y: number) => {
    const { getSeries, getPricePrecision, onPriceSet } = paramsRef.current;
    const grab = grabRef.current;
    const series = getSeries();
    if (!grab?.moved || !series) return;
    const price = series.coordinateToPrice(y);
    if (price == null || !Number.isFinite(price as number)) return;
    onPriceSet(grab.slot, roundToPricePrecision(price as number, getPricePrecision()));
  }, []);

  /**
   * Show or hide the chart's crosshair.
   *
   * `applyOptions` deep-merges, so naming only the two lines leaves the
   * crosshair's mode and colours alone — and the restore puts back the
   * library's defaults for exactly the four flags taken away.
   */
  const setCrosshairVisible = useCallback((visible: boolean) => {
    paramsRef.current.getChart()?.applyOptions({
      crosshair: {
        horzLine: { visible, labelVisible: visible },
        vertLine: { visible, labelVisible: visible },
      },
    });
  }, []);

  const cancelFrame = () => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
  };

  /**
   * Give the chart its panning back and forget the gesture.
   *
   * Reached from pointerup, from pointercancel, and from the unmount cleanup —
   * a drag interrupted by the component going away must never leave the chart
   * unpannable, which is why this hook is installed above the effect that owns
   * the chart, so this cleanup runs while the chart is still there.
   */
  const endGrab = useCallback(() => {
    if (grabRef.current === null) return;
    grabRef.current = null;
    cancelFrame();
    pendingYRef.current = null;
    const { getChart, getContainer } = paramsRef.current;
    const pointerId = pointerIdRef.current;
    pointerIdRef.current = null;
    if (pointerId !== null) {
      try {
        getContainer()?.releasePointerCapture?.(pointerId);
      } catch {
        /* the pointer was already gone */
      }
    }
    getChart()?.applyOptions({ handleScroll: true, handleScale: true });
    // Unconditional, like the panning handback: a press that never passed the
    // slop never hid it, and putting back what is already there costs nothing
    // next to leaving a chart permanently crosshair-less.
    setCrosshairVisible(true);
  }, [setCrosshairVisible]);

  useEffect(() => endGrab, [endGrab]);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    // Shift belongs to the measure tool, and only a primary press grabs.
    if (e.button !== 0 || e.shiftKey) return;
    const { getSeries, getTargets, getChart, getContainer } = paramsRef.current;
    const series = getSeries();
    const y = rowOf(e.clientY);
    if (!series || y === null) return;

    const target = hitTargetAt(
      getTargets(),
      (price) => {
        const coordinate = series.priceToCoordinate(price);
        return coordinate == null ? null : (coordinate as number);
      },
      y,
    );
    if (!target) return;

    grabRef.current = { slot: target.slot, originX: e.clientX, originY: e.clientY, moved: false };
    pointerIdRef.current = e.pointerId ?? null;
    if (e.pointerId != null) {
      try {
        getContainer()?.setPointerCapture?.(e.pointerId);
      } catch {
        /* capture is a convenience; the drag still tracks without it */
      }
    }
    // The chart pans on a pressed move by default, which would fight the
    // gesture for the same pixels. Handed back in `endGrab`, always — including
    // when the press turns out to have been a plain click.
    getChart()?.applyOptions({ handleScroll: false, handleScale: false });
  }, []);

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const grab = grabRef.current;
      if (!grab) return;
      if (!grab.moved) {
        const travelled = Math.hypot(e.clientX - grab.originX, e.clientY - grab.originY);
        // Below the slop nothing is written at all: the press is still a
        // candidate click, and a jittery finger must not set a price.
        if (travelled <= paramsRef.current.slopPx) return;
        grab.moved = true;
        // Hidden here rather than on the press: a stationary click keeps its
        // crosshair, and only a gesture that is really moving a line pays for
        // the label going away.
        setCrosshairVisible(false);
      }
      const y = rowOf(e.clientY);
      if (y === null) return;
      pendingYRef.current = y;
      if (frameRef.current !== null) return;
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = null;
        const pending = pendingYRef.current;
        pendingYRef.current = null;
        if (pending !== null) commit(pending);
      });
    },
    [commit, setCrosshairVisible],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent): boolean => {
      const grab = grabRef.current;
      if (!grab) return false;
      const wasDrag = grab.moved;
      if (wasDrag) {
        // The frame the release interrupted still has to land, or the line would
        // stop one coalescing window short of where the pointer let go.
        const y = rowOf(e.clientY);
        cancelFrame();
        const pending = y ?? pendingYRef.current;
        pendingYRef.current = null;
        if (pending !== null) commit(pending);
      }
      endGrab();
      return wasDrag;
    },
    [commit, endGrab],
  );

  const isDragging = useCallback(() => grabRef.current?.moved === true, []);

  return { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: endGrab, isDragging };
}
