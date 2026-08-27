/**
 * The two invariants the shared PNL chart shell owns (ARCH-242).
 *
 * Both of them fail *silently* — no throw, no console error, just two panes
 * quietly disagreeing about which pixel is which instant, or two charts on one
 * page quietly sharing a gradient — which is exactly why they are worth a test
 * rather than a comment: every presentation item that follows edits this one
 * component, and a broken gutter shows up as a chart that is subtly wrong, not
 * as a red build.
 *
 * What this file does NOT do is check the drawing. recharts measures its own
 * container, and jsdom has no layout, so a ResponsiveContainer here is 0x0 and
 * an SVG assertion would be theatre. So recharts is mocked with stubs that
 * record the props each element is given, and the assertions are about the
 * geometry contract and the element identities — the things that are decided in
 * our code and are true regardless of layout. The pixels stay a human's job.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AXIS_WIDTH, type PnlChartPoint } from "@/lib/pnl-chart";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

type Recorded = { type: string; props: Record<string, unknown> };

const { recorded } = vi.hoisted(() => ({ recorded: [] as Recorded[] }));

vi.mock("recharts", async () => {
  const { createElement } = await import("react");
  const stub = (type: string) => {
    const Stub = (props: Record<string, unknown>) => {
      recorded.push({ type, props });
      return createElement("div", { "data-rc": type }, props.children as never);
    };
    Stub.displayName = type;
    return Stub;
  };
  return {
    Area: stub("Area"),
    CartesianGrid: stub("CartesianGrid"),
    ComposedChart: stub("ComposedChart"),
    Legend: stub("Legend"),
    Line: stub("Line"),
    ReferenceLine: stub("ReferenceLine"),
    ResponsiveContainer: stub("ResponsiveContainer"),
    Tooltip: stub("Tooltip"),
    XAxis: stub("XAxis"),
    YAxis: stub("YAxis"),
  };
});

const { PnlEvolutionChart } = await import("./PnlEvolutionChart");

/** Two points, no position held anywhere on the timeline. */
const flat: PnlChartPoint[] = [
  { time: 1_000, realized: 10, unrealized: 2, total: 12, volume: 500, position: 0 },
  { time: 2_000, realized: 20, unrealized: -3, total: 17, volume: 900, position: 0 },
];

/** The same timeline, but the controller is holding something. */
const withPosition: PnlChartPoint[] = [
  flat[0],
  { ...flat[1], position: 1_234 },
];

/**
 * Split the recorded elements into panes: every ComposedChart opens one, and
 * everything recorded after it belongs to it (React renders parents before
 * children and siblings in order).
 */
function panes(): Recorded[][] {
  const out: Recorded[][] = [];
  for (const r of recorded) {
    if (r.type === "ComposedChart") out.push([]);
    if (out.length > 0) out[out.length - 1].push(r);
  }
  return out;
}

/** Total px each pane reserves on the left and on the right of its plot area. */
function gutters(pane: Recorded[]): { left: number; right: number } {
  let left = 0;
  let right = 0;
  for (const r of pane) {
    if (r.type !== "YAxis") continue;
    const width = r.props.width as number;
    if (r.props.orientation === "right") right += width;
    else left += width;
  }
  return { left, right };
}

let container: HTMLDivElement;
let root: Root;

function render(node: React.ReactNode) {
  act(() => {
    root.render(node);
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  recorded.length = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("PnlEvolutionChart pane geometry", () => {
  it("gives both panes the same gutters when there is no position", () => {
    render(<PnlEvolutionChart data={flat} title="PnL" pnlHeight={220} volumeHeight={120} />);

    const [top, bottom] = panes();
    expect(panes()).toHaveLength(2);
    expect(gutters(top)).toEqual(gutters(bottom));
    // Both sides are always reserved, so the plot area never moves when a
    // position appears or closes.
    expect(gutters(top)).toEqual({ left: AXIS_WIDTH, right: AXIS_WIDTH });
  });

  it("gives both panes the same gutters when a position is held", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);

    const [top, bottom] = panes();
    expect(gutters(top)).toEqual(gutters(bottom));
    expect(gutters(bottom)).toEqual({ left: AXIS_WIDTH, right: AXIS_WIDTH });
  });

  it("reads every axis width from AXIS_WIDTH rather than a literal", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);

    const axes = recorded.filter((r) => r.type === "YAxis");
    expect(axes).toHaveLength(4);
    for (const axis of axes) expect(axis.props.width).toBe(AXIS_WIDTH);
  });

  it("labels the position axis only when there is a position to label", () => {
    render(<PnlEvolutionChart data={flat} title="PnL" pnlHeight={220} volumeHeight={120} />);
    const quiet = recorded.find((r) => r.type === "YAxis" && r.props.yAxisId === "pos");
    expect(quiet?.props.tick).toBe(false);
    // ...and draws no position series at all.
    expect(recorded.some((r) => r.type === "Line" && r.props.dataKey === "position")).toBe(false);

    recorded.length = 0;
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);
    const labelled = recorded.find((r) => r.type === "YAxis" && r.props.yAxisId === "pos");
    expect(labelled?.props.tick).toBeTruthy();
    expect(recorded.some((r) => r.type === "Line" && r.props.dataKey === "position")).toBe(true);
  });

  it("passes the requested heights straight through to the two panes", () => {
    render(<PnlEvolutionChart data={flat} title="PnL" pnlHeight={130} volumeHeight={70} />);

    const heights = recorded.filter((r) => r.type === "ResponsiveContainer").map((r) => r.props.height);
    expect(heights).toEqual([130, 70]);
  });
});

describe("PnlEvolutionChart instance identity", () => {
  it("syncs its own two panes together", () => {
    render(<PnlEvolutionChart data={flat} title="PnL" pnlHeight={220} volumeHeight={120} />);

    const syncIds = recorded.filter((r) => r.type === "ComposedChart").map((r) => r.props.syncId);
    expect(syncIds).toHaveLength(2);
    expect(syncIds[0]).toBeTruthy();
    expect(syncIds[1]).toBe(syncIds[0]);
  });

  it("does not sync or share a gradient with a second chart on the same page", () => {
    // ControllerBrowser's modal opens over the still-mounted aggregated chart.
    render(
      <>
        <PnlEvolutionChart data={flat} title="Portfolio PnL" pnlHeight={220} volumeHeight={120} />
        <PnlEvolutionChart data={withPosition} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />
      </>,
    );

    const syncIds = recorded.filter((r) => r.type === "ComposedChart").map((r) => r.props.syncId);
    expect(new Set(syncIds).size).toBe(2); // two charts, two sync groups, two panes each

    const gradientIds = [...container.querySelectorAll("linearGradient")].map((g) => g.id);
    expect(gradientIds).toHaveLength(2);
    expect(new Set(gradientIds).size).toBe(2);
    for (const id of gradientIds) expect(id).not.toMatch(/[^a-zA-Z0-9]/);

    // Each area fills from its own gradient, not from whichever mounted first.
    const fills = recorded.filter((r) => r.type === "Area").map((r) => r.props.fill);
    expect(fills).toEqual(gradientIds.map((id) => `url(#${id})`));
  });
});

describe("PnlEvolutionChart header", () => {
  it("shows the Pos stat whenever a position is held, for every caller", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);
    expect(container.textContent).toContain("Pos:");

    recorded.length = 0;
    render(<PnlEvolutionChart data={flat} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);
    expect(container.textContent).not.toContain("Pos:");
  });

  it("renders the caller's title and filter row", () => {
    render(
      <PnlEvolutionChart
        data={flat}
        title="Portfolio PnL"
        pnlHeight={220}
        volumeHeight={120}
        filters={<div>chips go here</div>}
      />,
    );

    expect(container.textContent).toContain("Portfolio PnL");
    expect(container.textContent).toContain("chips go here");
  });
});
