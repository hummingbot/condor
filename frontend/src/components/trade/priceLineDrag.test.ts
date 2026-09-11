/**
 * The grab region for a draggable price line (FEAT-080).
 *
 * `hitTargetAt` is the whole of "is the pointer on a line", shared by the
 * primitive that sets the hover cursor and by the gesture that runs the same
 * test at pointerdown. It is pure, so it is tested without a chart.
 */

import { describe, expect, it, vi } from "vitest";

import type { PickSlot } from "@/components/executor/types";
import {
  createDragHitPrimitive,
  dragExternalId,
  hitTargetAt,
  type DragTarget,
} from "./priceLineDrag";

/** A steep, invertible scale: price 100 sits at row 300, price 200 at row 200. */
function toCoordinate(price: number): number | null {
  return 400 - price;
}

const TARGETS: DragTarget[] = [
  { slot: "start", price: 100 }, // row 300
  { slot: "end", price: 200 }, // row 200
  { slot: "limit", price: 50 }, // row 350
];

describe("hitTargetAt", () => {
  it("finds the line under the pointer", () => {
    expect(hitTargetAt(TARGETS, toCoordinate, 300)?.slot).toBe("start");
    expect(hitTargetAt(TARGETS, toCoordinate, 200)?.slot).toBe("end");
  });

  it("grabs within the tolerance and lets go past it", () => {
    // `start` is at row 300; the default tolerance is 5px.
    expect(hitTargetAt(TARGETS, toCoordinate, 305)?.slot).toBe("start");
    expect(hitTargetAt(TARGETS, toCoordinate, 295)?.slot).toBe("start");
    expect(hitTargetAt(TARGETS, toCoordinate, 306)).toBeNull();
    expect(hitTargetAt(TARGETS, toCoordinate, 294)).toBeNull();
  });

  it("takes the nearest of two lines close together", () => {
    // A grid squeezed tight: `limit` at row 297, `start` at row 300.
    const crowded: DragTarget[] = [
      { slot: "start", price: 100 },
      { slot: "limit", price: 103 },
    ];
    expect(hitTargetAt(crowded, toCoordinate, 298)?.slot).toBe("limit");
    expect(hitTargetAt(crowded, toCoordinate, 300)?.slot).toBe("start");
  });

  it("cannot grab a line the scale has scrolled off", () => {
    const offScale = (price: number) => (price === 100 ? null : toCoordinate(price));
    expect(hitTargetAt(TARGETS, offScale, 300)).toBeNull();
    // The other lines are still grabbable.
    expect(hitTargetAt(TARGETS, offScale, 200)?.slot).toBe("end");
  });

  it("answers null for an empty target list", () => {
    expect(hitTargetAt([], toCoordinate, 300)).toBeNull();
  });

  it("honours an explicit tolerance", () => {
    expect(hitTargetAt(TARGETS, toCoordinate, 320, 25)?.slot).toBe("start");
    expect(hitTargetAt(TARGETS, toCoordinate, 320, 5)).toBeNull();
  });
});

describe("createDragHitPrimitive", () => {
  /** Attach the primitive to a stand-in series carrying the same scale. */
  function attached(targets: DragTarget[] = TARGETS) {
    const series = {
      priceToCoordinate: vi.fn((price: number) => toCoordinate(price)),
    };
    const primitive = createDragHitPrimitive(() => targets);
    primitive.attached?.({ series } as never);
    return { primitive, series };
  }

  it("reports the slot and a resize cursor over a line", () => {
    const { primitive } = attached();
    const hit = primitive.hitTest?.(50, 200);
    expect(hit).toEqual({
      externalId: dragExternalId("end" as PickSlot),
      cursorStyle: "ns-resize",
      zOrder: "top",
    });
  });

  it("reports nothing away from every line", () => {
    const { primitive } = attached();
    expect(primitive.hitTest?.(50, 250)).toBeNull();
  });

  it("reads the targets fresh on every hit test", () => {
    const targets: DragTarget[] = [];
    const series = { priceToCoordinate: (price: number) => toCoordinate(price) };
    const primitive = createDragHitPrimitive(() => targets);
    primitive.attached?.({ series } as never);

    expect(primitive.hitTest?.(50, 300)).toBeNull();
    targets.push({ slot: "start", price: 100 });
    expect(primitive.hitTest?.(50, 300)?.externalId).toBe(dragExternalId("start"));
  });

  it("goes inert once detached", () => {
    const { primitive } = attached();
    primitive.detached?.();
    expect(primitive.hitTest?.(50, 200)).toBeNull();
  });
});
