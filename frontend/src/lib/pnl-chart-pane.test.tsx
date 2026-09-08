/**
 * The activity pane's shared wiring (ARCH-341).
 *
 * The pure helpers are pinned in `pnl-chart.test.ts`; what this file pins is
 * the part that used to be copied into both charts and could therefore drift:
 * the *plot* width the bars are sized from — the container minus both axis
 * gutters and the right margin, not the container itself — the bar shape's
 * re-centring and its forwarding of the cosmetic props, and the position
 * gradient's offset being measured against the area's own signed extent rather
 * than the padded axis domain.
 *
 * Needs a DOM to hold the hook's state, so this file overrides vitest's default
 * `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { BarShapeProps } from "recharts";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  AXIS_WIDTH,
  PANE_MARGIN_RIGHT,
  chartBucketMs,
  positionAreaExtent,
  positionAxisDomain,
  volumeBarWidth,
  zeroGradientOffset,
  type PnlChartPoint,
} from "./pnl-chart";
import { useActivityPane, type ActivityPane } from "./pnl-chart-pane";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const BUCKET = 5 * 60_000;

/** A window of `count` points a bucket apart, with the given net positions. */
function points(positions: number[]): PnlChartPoint[] {
  return positions.map((position, index) => ({
    time: 1_700_000_000_000 + index * BUCKET,
    realized: 0,
    unrealized: 0,
    total: 0,
    volume: index * 100,
    volumeDelta: 100,
    position,
  }));
}

function spanOf(data: PnlChartPoint[]): number {
  return data.length > 1 ? data[data.length - 1].time - data[0].time : 0;
}

let container: HTMLDivElement;
let root: Root;

/**
 * Mount the hook over `data` and hand back its live result.
 *
 * The result is published from an effect rather than assigned during render:
 * writing to a captured variable while rendering is a side effect the
 * react-hooks rules reject, test harness or not. `act` flushes the effect, so
 * the value is there by the time the caller reads it.
 */
function mount(data: PnlChartPoint[]): { pane: () => ActivityPane } {
  let latest: ActivityPane | null = null;
  const publish = (pane: ActivityPane) => {
    latest = pane;
  };
  function Harness() {
    const pane = useActivityPane(data, spanOf(data));
    useEffect(() => {
      publish(pane);
    });
    return null;
  }
  act(() => {
    root.render(<Harness />);
  });
  return {
    pane: () => {
      if (!latest) throw new Error("hook did not run");
      return latest;
    },
  };
}

/** The rect the pane would draw for one bar, given recharts' own placement. */
function drawnBar(pane: ActivityPane, from: Partial<BarShapeProps> = {}) {
  const props = {
    x: 100,
    y: 20,
    width: 4,
    height: 60,
    fill: "#3b82f6",
    fillOpacity: 0.45,
    radius: [2, 2, 0, 0],
    ...from,
  } as unknown as BarShapeProps;
  return pane.volumeBar(props).props as {
    x: number;
    width: number;
    radius?: unknown;
    fillOpacity?: number;
    fill?: string;
  };
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("useActivityPane", () => {
  it("sizes a bar from the plot area, not the container", () => {
    // Enough buckets that the bar sits below VOLUME_BAR_MAX_PX at both widths,
    // or the clamp would hide the difference the subtraction makes.
    const data = points(new Array(40).fill(0));
    const { pane } = mount(data);
    // Nothing is measured yet, so the width is recharts' own.
    expect(drawnBar(pane()).width).toBe(4);

    const containerWidth = 640;
    act(() => pane().onActivityResize(containerWidth));

    const span = spanOf(data);
    const bucketMs = chartBucketMs(data);
    const expected = volumeBarWidth(
      containerWidth - 2 * AXIS_WIDTH - PANE_MARGIN_RIGHT,
      span,
      bucketMs,
    );
    expect(expected).toBeDefined();
    expect(drawnBar(pane()).width).toBeCloseTo(expected as number, 10);
    // The subtraction is load-bearing: the whole container would be wider.
    expect(expected).toBeLessThan(volumeBarWidth(containerWidth, span, bucketMs) as number);
  });

  it("centres the bar on its instant and forwards the cosmetic props", () => {
    const data = points(new Array(40).fill(0));
    const { pane } = mount(data);
    act(() => pane().onActivityResize(640));

    const rect = drawnBar(pane(), { x: 100, width: 4 });
    // recharts starts a bar at its instant; the pane draws it centred there.
    expect(rect.x).toBeCloseTo(100 + 4 / 2 - rect.width / 2, 10);
    expect(rect.radius).toEqual([2, 2, 0, 0]);
    expect(rect.fillOpacity).toBe(0.45);

    const bare = drawnBar(pane(), { radius: undefined, fillOpacity: undefined });
    expect(bare.radius).toBeUndefined();
    expect(bare.fillOpacity).toBeUndefined();
  });

  it("puts the gradient's zero on the signed area's own extent", () => {
    const data = points([0, 400, -100, 200]);
    const { pane } = mount(data);

    const extent = positionAreaExtent(data);
    expect(extent).toEqual([-100, 400]);
    expect(pane().positionZeroOffset).toBeCloseTo(zeroGradientOffset(extent), 12);
    expect(pane().positionZeroOffset).toBeGreaterThan(0);
    expect(pane().positionZeroOffset).toBeLessThan(1);
    // Not the padded domain: that would put the colour change off the baseline.
    expect(pane().positionZeroOffset).not.toBeCloseTo(
      zeroGradientOffset(positionAxisDomain(data)),
      12,
    );
    expect(pane().positionDomain).toEqual(positionAxisDomain(data));
  });

  it("names the window's bucket", () => {
    const { pane } = mount(points([0, 0, 0, 0]));
    expect(pane().bucketMs).toBe(BUCKET);
    expect(pane().bucketLabel).toBe("5m");
  });
});
