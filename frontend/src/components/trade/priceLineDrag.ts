// ── Which price lines can be grabbed, and where they are ──
//
// The lines the chart draws for `start` / `end` / `limit` (and a panel's extra
// line that declares a slot) are `createPriceLine` objects: canvas paint with no
// hit-testing and no mouse API in lightweight-charts 5. Nothing can be bound to
// them, so the grab region is built beside them — derived from the same `price`
// by the same price→pixel mapping, evaluated on demand, in the frame the pointer
// moved. There is no cached geometry to fall out of sync with the paint.
//
// `hitTargetAt` is exported rather than kept private to the primitive because it
// has two callers: the primitive answers the library's hover query so the cursor
// turns, and the gesture hook runs the same test at pointerdown — which is what
// makes touch work, since a finger that lands on a line was never hovering it.

import type { ISeriesPrimitive, SeriesAttachedParameter } from "lightweight-charts";

import type { PickSlot } from "@/components/executor/types";

/** A price line the user may grab, named by the slot it writes back to. */
export interface DragTarget {
  slot: PickSlot;
  price: number;
}

/** How far above or below a line still counts as grabbing it. */
export const DRAG_TOLERANCE_PX = 5;

/**
 * The draggable target whose line sits within `tol` pixels of `y`, nearest first.
 *
 * `toCoordinate` is the series' price→pixel mapping; it answers `null` for a
 * price scrolled off the visible scale, and such a target simply cannot be
 * grabbed. Returns `null` when nothing is near enough.
 */
export function hitTargetAt(
  targets: readonly DragTarget[],
  toCoordinate: (price: number) => number | null,
  y: number,
  tol: number = DRAG_TOLERANCE_PX,
): DragTarget | null {
  let best: DragTarget | null = null;
  let bestDistance = Infinity;
  for (const target of targets) {
    const coordinate = toCoordinate(target.price);
    if (coordinate == null || !Number.isFinite(coordinate)) continue;
    const distance = Math.abs(coordinate - y);
    if (distance <= tol && distance < bestDistance) {
      bestDistance = distance;
      best = target;
    }
  }
  return best;
}

/** The `externalId` a hit reports, so a crosshair subscriber can name the slot. */
export function dragExternalId(slot: PickSlot): string {
  return `drag:${slot}`;
}

/**
 * A series primitive that draws nothing and only reports hits.
 *
 * It exists for the cursor alone: the library calls `hitTest` on every mouse
 * move and applies the returned `cursorStyle` to the pane element, so the
 * pointer becomes `ns-resize` over a draggable line and nothing else changes.
 * A primitive with no `paneViews` is still hit-tested — the pane maps over its
 * primitives directly — so the existing rendering path stays untouched.
 *
 * `zOrder: "top"` makes this hit beat the built-in ones, which is what puts the
 * resize cursor above a series marker sharing the row.
 */
export function createDragHitPrimitive(
  getTargets: () => readonly DragTarget[],
): ISeriesPrimitive {
  let series: SeriesAttachedParameter["series"] | null = null;

  return {
    attached(param: SeriesAttachedParameter) {
      series = param.series;
    },
    detached() {
      series = null;
    },
    hitTest(_x: number, y: number) {
      const current = series;
      if (!current) return null;
      const target = hitTargetAt(
        getTargets(),
        (price) => {
          const coordinate = current.priceToCoordinate(price);
          return coordinate == null ? null : (coordinate as number);
        },
        y,
      );
      if (!target) return null;
      return {
        externalId: dragExternalId(target.slot),
        cursorStyle: "ns-resize",
        zOrder: "top" as const,
      };
    },
  };
}
