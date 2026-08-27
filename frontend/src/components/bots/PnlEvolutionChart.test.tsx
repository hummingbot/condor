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

import { act, cloneElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatAxisTime } from "@/lib/formatters";
import {
  AXIS_WIDTH,
  PANE_MARGIN_RIGHT,
  PANE_PAD_X,
  PLOT_INSET_LEFT,
  PLOT_INSET_RIGHT,
  PNL_SERIES_LABELS,
  positionAreaExtent,
  positionAxisDomain,
  zeroGradientOffset,
  type PnlChartPoint,
} from "@/lib/pnl-chart";
import { getThemeColors } from "@/lib/theme-colors";

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
    Bar: stub("Bar"),
    CartesianGrid: stub("CartesianGrid"),
    ComposedChart: stub("ComposedChart"),
    Legend: stub("Legend"),
    Line: stub("Line"),
    Rectangle: stub("Rectangle"),
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
  { time: 1_000, realized: 10, unrealized: 2, total: 12, volume: 500, volumeDelta: 500, position: 0 },
  { time: 2_000, realized: 20, unrealized: -3, total: 17, volume: 900, volumeDelta: 400, position: 0 },
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
    expect(recorded.some((r) => r.props.dataKey === "position")).toBe(false);

    recorded.length = 0;
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);
    const labelled = recorded.find((r) => r.type === "YAxis" && r.props.yAxisId === "pos");
    expect(labelled?.props.tick).toBeTruthy();
    expect(recorded.some((r) => r.props.dataKey === "position")).toBe(true);
  });

  it("passes the requested heights straight through to the two panes", () => {
    render(<PnlEvolutionChart data={flat} title="PnL" pnlHeight={130} volumeHeight={70} />);

    const heights = recorded.filter((r) => r.type === "ResponsiveContainer").map((r) => r.props.height);
    expect(heights).toEqual([130, 70]);
  });
});

describe("PnlEvolutionChart signed position area", () => {
  const series = (...positions: number[]): PnlChartPoint[] =>
    positions.map((position, i) => ({ ...flat[i % flat.length], time: 1_000 * (i + 1), position }));

  const longOnly = series(400, 1_200);
  const shortOnly = series(-400, -1_200);
  const flipped = series(900, -500);

  /** The bottom pane's position axis, as recharts was told to draw it. */
  function positionAxis(): Record<string, unknown> {
    const axis = recorded.find((r) => r.type === "YAxis" && r.props.yAxisId === "pos");
    expect(axis).toBeDefined();
    return axis!.props;
  }

  /** The two stops of the signed-area gradient: [offset, color] each. */
  function fillStops(): Array<[number, string]> {
    const gradient = container.querySelector("[id^=posGrad]");
    return [...(gradient?.querySelectorAll("stop") ?? [])].map((s) => [
      Number(s.getAttribute("offset")),
      String(s.getAttribute("stop-color")),
    ]);
  }

  it("draws the position as an area filled from zero, not as a bare line", () => {
    render(<PnlEvolutionChart data={flipped} title="PnL" pnlHeight={220} volumeHeight={120} />);

    const area = recorded.find((r) => r.type === "Area" && r.props.dataKey === "position");
    expect(area).toBeDefined();
    // Pinned explicitly: recharts bases an area at the domain edge, not at
    // zero, whenever the domain does not straddle zero.
    expect(area!.props.baseValue).toBe(0);
    expect(area!.props.yAxisId).toBe("pos");
    expect(recorded.some((r) => r.type === "Line" && r.props.dataKey === "position")).toBe(false);
    // ...and volume is bars on its own axis (READ-245), so the pane's two
    // series no longer share a shape at all.
    const volume = recorded.find((r) => r.props.dataKey === "volumeDelta");
    expect(volume?.type).toBe("Bar");
    expect(volume?.props.yAxisId).toBe("vol");
  });

  it("keeps zero inside the position axis whichever side the book is on", () => {
    for (const data of [longOnly, shortOnly, flipped]) {
      recorded.length = 0;
      render(<PnlEvolutionChart data={data} title="PnL" pnlHeight={220} volumeHeight={120} />);

      const [min, max] = positionAxis().domain as [number, number];
      // Strictly straddles: an all-long book must not have its baseline lying
      // on the pane's bottom edge, where it would read as a border.
      expect(min).toBeLessThan(0);
      expect(max).toBeGreaterThan(0);
      // ...and still shows the whole series, so recharts never has to widen it.
      expect(min).toBeLessThanOrEqual(Math.min(...data.map((p) => p.position)));
      expect(max).toBeGreaterThanOrEqual(Math.max(...data.map((p) => p.position)));
    }
  });

  it("splits the fill at the baseline: long above it, short below it", () => {
    const tc = getThemeColors();

    render(<PnlEvolutionChart data={flipped} title="PnL" pnlHeight={220} volumeHeight={120} />);
    const stops = fillStops();
    expect(stops).toHaveLength(2);
    // Both stops sit on the same offset — a hard colour change, not a blend.
    const zero = zeroGradientOffset(positionAreaExtent(flipped));
    expect(stops[0][0]).toBeCloseTo(zero, 10);
    expect(stops[1][0]).toBeCloseTo(zero, 10);
    expect(stops[0][1]).toBe(tc.up); // above zero: net long
    expect(stops[1][1]).toBe(tc.down); // below zero: net short
    // 900 up, 500 down: the split sits where the fill actually crosses zero.
    expect(zero).toBeCloseTo(900 / 1_400, 10);
  });

  it("measures the split against the fill's own box, not the padded axis", () => {
    // A gradient is in objectBoundingBox units, so its 0..1 runs over the
    // filled path — which stops at the data — and not over the axis, which is
    // padded past it. Taking the offset from the domain would paint a band of
    // the wrong colour along the baseline of a book that never changes sign.
    render(<PnlEvolutionChart data={longOnly} title="PnL" pnlHeight={220} volumeHeight={120} />);
    expect(fillStops()[0][0]).toBe(1); // all long: no short colour at all
    const domainZero = zeroGradientOffset(positionAxis().domain as [number, number]);
    expect(domainZero).toBeLessThan(1); // ...which the padded domain would not give

    recorded.length = 0;
    render(<PnlEvolutionChart data={shortOnly} title="PnL" pnlHeight={220} volumeHeight={120} />);
    expect(fillStops()[0][0]).toBe(0); // all short: no long colour at all
    expect(zeroGradientOffset(positionAxis().domain as [number, number])).toBeGreaterThan(0);
  });

  it("marks zero with a reference line exactly while the series is drawn", () => {
    render(<PnlEvolutionChart data={flipped} title="PnL" pnlHeight={220} volumeHeight={120} />);
    const zeroLines = recorded.filter((r) => r.type === "ReferenceLine" && r.props.yAxisId === "pos");
    expect(zeroLines).toHaveLength(1);
    expect(zeroLines[0].props.y).toBe(0);
    // Drawn after the fill, so the baseline stays legible through it.
    const order = (pred: (r: Recorded) => boolean) => recorded.findIndex(pred);
    expect(order((r) => r.type === "ReferenceLine" && r.props.yAxisId === "pos")).toBeGreaterThan(
      order((r) => r.type === "Area" && r.props.dataKey === "position"),
    );

    recorded.length = 0;
    render(<PnlEvolutionChart data={flat} title="PnL" pnlHeight={220} volumeHeight={120} />);
    expect(recorded.some((r) => r.type === "ReferenceLine" && r.props.yAxisId === "pos")).toBe(false);
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

    const allGradientIds = [...container.querySelectorAll("linearGradient")].map((g) => g.id);
    for (const id of allGradientIds) expect(id).not.toMatch(/[^a-zA-Z0-9]/);
    expect(new Set(allGradientIds).size).toBe(allGradientIds.length);

    // Each pane's area fills from its own instance's gradient, not from
    // whichever chart mounted first: the PNL area from that instance's
    // pnlGrad, the position area from its posGrad.
    for (const prefix of ["pnlGrad", "posGrad"]) {
      const ids = allGradientIds.filter((id) => id.startsWith(prefix));
      const fills = recorded
        .filter((r) => r.type === "Area" && String(r.props.fill).includes(prefix))
        .map((r) => r.props.fill);
      expect(fills).toEqual(ids.map((id) => `url(#${id})`));
    }
    // Every chart has a PNL gradient; only the one actually holding a position
    // pays for the signed-area one.
    expect(allGradientIds.filter((id) => id.startsWith("pnlGrad"))).toHaveLength(2);
    expect(allGradientIds.filter((id) => id.startsWith("posGrad"))).toHaveLength(1);
  });
});

describe("PnlEvolutionChart header legend", () => {
  const FIFTEEN_MIN = 15 * 60_000;
  const t0 = new Date(2026, 2, 14, 8, 30).getTime();

  /** Traded $400 over the window on screen, out of $5,400 traded since deploy. */
  const traded: PnlChartPoint[] = [
    { time: t0, realized: 10, unrealized: 2, total: 12, volume: 5_000, volumeDelta: 0, position: 0 },
    { time: t0 + FIFTEEN_MIN, realized: 20, unrealized: -3, total: 17, volume: 5_400, volumeDelta: 400, position: 0 },
  ];
  const tradedShort: PnlChartPoint[] = [traded[0], { ...traded[1], position: -1_234 }];

  /** Every legend row, keyed by the series it names. */
  function entries(): Record<string, HTMLElement> {
    const out: Record<string, HTMLElement> = {};
    for (const el of container.querySelectorAll("[data-legend-entry]"))
      out[el.getAttribute("data-legend-entry")!] = el as HTMLElement;
    return out;
  }

  /** The series listed under one group, in order. */
  function grouped(label: string): string[] {
    const group = container.querySelector(`[data-legend-group="${label}"]`);
    return [...(group?.querySelectorAll("[data-legend-entry]") ?? [])].map(
      (el) => el.getAttribute("data-legend-entry")!,
    );
  }

  it("names every drawn series in words, permanently, without hovering", () => {
    render(<PnlEvolutionChart data={tradedShort} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);

    // All five, including the two that used to be identifiable only by matching
    // a stroke colour to a coloured Y-axis tick.
    for (const [series, label] of Object.entries(PNL_SERIES_LABELS)) {
      expect(entries()[series], `no legend entry for ${series}`).toBeDefined();
      expect(entries()[series].textContent).toContain(label);
    }
  });

  it("is the only legend: the recharts <Legend> is gone from both panes", () => {
    render(<PnlEvolutionChart data={tradedShort} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);
    expect(recorded.some((r) => r.type === "Legend")).toBe(false);
  });

  it("groups the series by pane, under the panes' own names", () => {
    render(<PnlEvolutionChart data={tradedShort} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);

    // The groups are named exactly as the pane captions are — that shared word
    // is the only thing pointing a group at the pane it describes.
    const captions = [...container.querySelectorAll("[data-pane-caption]")].map((c) =>
      c.textContent!.toLowerCase(),
    );
    const groups = [...container.querySelectorAll("[data-legend-group]")].map((g) =>
      g.getAttribute("data-legend-group"),
    );
    expect(groups).toEqual(captions);

    expect(grouped("pnl")).toEqual(["total", "realized", "unrealized"]);
    expect(grouped("activity")).toEqual(["volumeDelta", "position", "volume"]);
  });

  it("draws each swatch as the mark that series is actually drawn with", () => {
    render(<PnlEvolutionChart data={tradedShort} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);
    const swatch = (series: string) => entries()[series].querySelector("svg")!;
    const drawn = (type: string, dataKey: string) =>
      recorded.find((r) => r.type === type && r.props.dataKey === dataKey)!.props;

    // Strokes are read off the series themselves, so a restyled line cannot
    // leave the legend describing the old one.
    const realized = swatch("realized").querySelector("line")!;
    expect(realized.getAttribute("stroke")).toBe(drawn("Line", "realized").stroke);
    expect(realized.getAttribute("stroke-dasharray")).toBeNull();

    const unrealized = swatch("unrealized").querySelector("line")!;
    expect(unrealized.getAttribute("stroke")).toBe(drawn("Line", "unrealized").stroke);
    expect(unrealized.getAttribute("stroke-dasharray")).toBe(drawn("Line", "unrealized").strokeDasharray);

    // Total: a stroke over the tint its area is filled with.
    expect(swatch("total").querySelector("rect")).toBeTruthy();
    expect(swatch("total").querySelector("line")).toBeTruthy();

    // Volume is a Bar, so its swatch is bars — not the little line recharts
    // would have drawn for it.
    expect(swatch("volumeDelta").querySelectorAll("rect").length).toBeGreaterThan(1);
    expect(swatch("volumeDelta").querySelector("line")).toBeNull();
    expect(swatch("volumeDelta").querySelector("rect")!.getAttribute("fill")).toBe(
      drawn("Bar", "volumeDelta").fill,
    );

    // Position is a signed Area: a violet stroke over a fill that splits at a
    // dashed zero baseline, exactly as READ-246 draws it.
    const positionChip = swatch("position");
    expect(positionChip.querySelector("path")).toBeTruthy();
    expect(positionChip.querySelector("polyline")!.getAttribute("stroke")).toBe(
      drawn("Area", "position").stroke,
    );
    expect(positionChip.querySelector("line")!.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(positionChip.querySelectorAll("stop")).toHaveLength(2);

    // ...and the lifetime figure has no mark at all, because nothing draws it.
    expect(swatch("volume").children).toHaveLength(0);
  });

  it("keeps the bars' window total apart from the lifetime counter", () => {
    render(<PnlEvolutionChart data={traded} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);

    // The bars add up to what was traded over the window on screen...
    const bars = entries().volumeDelta.textContent!;
    expect(bars).toContain("$400");
    expect(bars).toContain("on screen");
    // ...each one covering a named slice of it, without which the number could
    // be a busy hour or a dead day.
    expect(bars).toContain("15m bars");

    // ...while the counter they were differenced from stays its own entry,
    // labelled as the different quantity it is.
    const lifetime = entries().volume.textContent!;
    expect(lifetime).toContain("$5.4K");
    expect(lifetime).toContain("lifetime");
  });

  it("totals the bars the pane can draw, not the ones it cannot", () => {
    // A snapshot whose volume did not arrive as a number folds to a NaN delta
    // and recharts draws no bar for it. The total has to agree with the bars
    // beside it, so it skips that one too rather than reading "$NaN".
    const withGap: PnlChartPoint[] = [
      traded[0],
      { ...traded[1], time: traded[1].time, volumeDelta: Number.NaN },
      { ...traded[1], time: traded[1].time + FIFTEEN_MIN, volumeDelta: 400 },
    ];
    render(<PnlEvolutionChart data={withGap} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);

    expect(entries().volumeDelta.textContent).toContain("$400");
    expect(entries().volumeDelta.textContent).not.toContain("NaN");
  });

  it("lists the position exactly while the position series is drawn", () => {
    render(<PnlEvolutionChart data={traded} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);
    expect(entries().position).toBeUndefined();
    expect(recorded.some((r) => r.type === "Area" && r.props.dataKey === "position")).toBe(false);

    recorded.length = 0;
    render(<PnlEvolutionChart data={tradedShort} title="PnL Evolution" pnlHeight={130} volumeHeight={70} />);
    expect(entries().position.textContent).toContain(PNL_SERIES_LABELS.position);
    expect(recorded.some((r) => r.type === "Area" && r.props.dataKey === "position")).toBe(true);
  });
});

describe("PnlEvolutionChart header", () => {
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

describe("PnlEvolutionChart X axis", () => {
  /** Both panes' X tick formatters, applied to one instant. */
  function xLabels(at: number): string[] {
    return recorded
      .filter((r) => r.type === "XAxis")
      .map((r) => (r.props.tickFormatter as (v: number) => string)(at));
  }

  const DAY = 86_400_000;
  const start = new Date(2026, 2, 14, 8, 30).getTime();
  const series = (spanMs: number): PnlChartPoint[] => [
    { ...flat[0], time: start },
    { ...flat[1], time: start + spanMs },
  ];

  it("labels a few hours with a bare time, and a multi-day window with the day (READ-250)", () => {
    render(<PnlEvolutionChart data={series(6 * 3_600_000)} title="PnL" pnlHeight={220} volumeHeight={120} />);
    expect(xLabels(start)).toEqual(["08:30", "08:30"]);

    recorded.length = 0;
    render(<PnlEvolutionChart data={series(5 * DAY)} title="PnL" pnlHeight={220} volumeHeight={120} />);
    expect(xLabels(start)).toEqual(["Mar 14 08:30", "Mar 14 08:30"]);
  });

  it("gives both panes the same formatter, so their columns read alike", () => {
    render(<PnlEvolutionChart data={series(400 * DAY)} title="PnL" pnlHeight={220} volumeHeight={120} />);
    const formatters = recorded.filter((r) => r.type === "XAxis").map((r) => r.props.tickFormatter);
    expect(formatters).toHaveLength(2);
    expect(formatters[1]).toBe(formatters[0]);
    expect(xLabels(start)).toEqual(["Mar '26", "Mar '26"]);
  });

  it("takes the span from the series' own ends, not from a fixed guess", () => {
    for (const span of [2 * 3_600_000, 3 * DAY, 30 * DAY, 400 * DAY]) {
      recorded.length = 0;
      render(<PnlEvolutionChart data={series(span)} title="PnL" pnlHeight={220} volumeHeight={120} />);
      expect(xLabels(start)).toEqual([formatAxisTime(start, span), formatAxisTime(start, span)]);
    }
  });

  it("survives a single-point series without a span to measure", () => {
    render(<PnlEvolutionChart data={[flat[0]]} title="PnL" pnlHeight={220} volumeHeight={120} />);
    expect(xLabels(start)).toEqual(["08:30", "08:30"]);
  });
});

describe("PnlEvolutionChart pane captions", () => {
  /** The two caption rows, in document order. */
  function captions(): HTMLElement[] {
    return [...container.querySelectorAll("[data-pane-caption]")] as HTMLElement[];
  }

  it("names each pane and rules the lower one off from the upper (READ-247)", () => {
    render(<PnlEvolutionChart data={flat} title="Portfolio PnL" pnlHeight={220} volumeHeight={120} />);

    expect(captions().map((c) => c.textContent)).toEqual(["PnL", "Activity"]);
    // Only the lower caption carries the rule; the upper one already has the
    // header's border above it, and two rules in a row would read as a gap.
    expect(captions()[0].className).not.toContain("border-t");
    expect(captions()[1].className).toContain("border-t");
  });

  it("insets the rule to the plot area the two panes share, not to the card", () => {
    render(<PnlEvolutionChart data={flat} title="Portfolio PnL" pnlHeight={220} volumeHeight={120} />);

    // jsdom has no layout, so what is checkable here is that the inset is the
    // one derived from AXIS_WIDTH rather than a literal that would silently
    // drift off the grid the moment the gutter contract changed.
    for (const caption of captions()) {
      expect(caption.style.marginLeft).toBe(`${PLOT_INSET_LEFT}px`);
      expect(caption.style.marginRight).toBe(`${PLOT_INSET_RIGHT}px`);
    }
    expect(PLOT_INSET_LEFT).toBeGreaterThan(AXIS_WIDTH);
  });

  it("keeps that inset true by drawing the panes' own padding and margin from it", () => {
    render(<PnlEvolutionChart data={flat} title="Portfolio PnL" pnlHeight={220} volumeHeight={120} />);

    const wrappers = [...container.querySelectorAll("[data-pane]")] as HTMLElement[];
    expect(wrappers).toHaveLength(2);
    for (const wrapper of wrappers) {
      expect(wrapper.style.paddingLeft).toBe(`${PANE_PAD_X}px`);
      expect(wrapper.style.paddingRight).toBe(`${PANE_PAD_X}px`);
    }

    const margins = recorded
      .filter((r) => r.type === "ComposedChart")
      .map((r) => r.props.margin as { left: number; right: number });
    expect(margins).toHaveLength(2);
    for (const margin of margins) {
      expect(margin.left).toBe(0);
      expect(margin.right).toBe(PANE_MARGIN_RIGHT);
    }
  });
});

describe("PnlEvolutionChart hover card (READ-248)", () => {
  /**
   * The `content` element each pane hands its <Tooltip>, as of the *latest*
   * render — `recorded` accumulates across re-renders, and hovering causes one.
   */
  function tooltipContents(): React.ReactElement<Record<string, unknown>>[] {
    return panes().slice(-2).map((pane) => {
      const tooltip = pane.find((r) => r.type === "Tooltip");
      if (!tooltip) throw new Error("pane has no Tooltip");
      return tooltip.props.content as React.ReactElement<Record<string, unknown>>;
    });
  }

  /**
   * Render one pane's card the way recharts would: clone its `content` element
   * with the active flag, the hovered row's payload and its label. The payload
   * carries only the series *that pane* draws — the point of the exercise.
   */
  function card(content: React.ReactElement<Record<string, unknown>>, keys: string[], row: PnlChartPoint) {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const hostRoot = createRoot(host);
    act(() => {
      hostRoot.render(
        cloneElement(content, {
          active: true,
          label: row.time,
          payload: keys.map((dataKey) => ({ dataKey, value: row[dataKey as keyof PnlChartPoint], payload: row })),
        }),
      );
    });
    const html = host.innerHTML;
    act(() => hostRoot.unmount());
    host.remove();
    return html;
  }

  const PNL_KEYS = ["total", "realized", "unrealized"];
  const ACTIVITY_KEYS = ["volumeDelta", "position"];

  /** Move the pointer into one pane, the way React derives enter/leave. */
  function hover(pane: "pnl" | "activity" | null) {
    const target = pane
      ? (container.querySelector(`[data-pane="${pane}"]`) as HTMLElement)
      : document.body;
    act(() => {
      target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body }));
    });
  }

  it("gives both panes the very same card, so either hover reads the same", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);

    const [top, bottom] = tooltipContents();
    expect(top.type).toBe(bottom.type);
  });

  it("draws exactly one card per hover — the pane under the pointer", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);

    // Nothing hovered: the panes are mounted but neither card is drawn.
    let [top, bottom] = tooltipContents();
    expect(top.props.visible).toBe(false);
    expect(bottom.props.visible).toBe(false);

    hover("pnl");
    [top, bottom] = tooltipContents();
    expect(top.props.visible).toBe(true);
    // The lower pane is active too — that is what syncId does — and still
    // draws nothing, which is the whole fix.
    expect(bottom.props.visible).toBe(false);
    expect(card(bottom, ACTIVITY_KEYS, withPosition[1])).toBe("");

    hover("activity");
    [top, bottom] = tooltipContents();
    expect(top.props.visible).toBe(false);
    expect(bottom.props.visible).toBe(true);
    expect(card(top, PNL_KEYS, withPosition[1])).toBe("");
  });

  it("reports both panes' series under one timestamp, from either pane's payload", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);
    hover("pnl");
    const [top] = tooltipContents();
    hover("activity");
    const [, bottom] = tooltipContents();

    // The activity pane's payload has no PnL series in it and the PnL pane's
    // has no volume — each card recovers the rest from the row the point was
    // drawn from, so the two read alike.
    const fromTop = card(top, PNL_KEYS, withPosition[1]);
    const fromBottom = card(bottom, ACTIVITY_KEYS, withPosition[1]);

    for (const html of [fromTop, fromBottom]) {
      for (const label of Object.values(PNL_SERIES_LABELS)) expect(html).toContain(label);
      // One header, not one per section.
      expect(html.match(/data-tooltip-row/g)).toHaveLength(5);
    }
    expect(fromTop).toBe(fromBottom);
  });

  it("keeps the position row at a long→short crossover, where the value is 0", () => {
    // A book that is net long, then flat, then net short: the flat instant is
    // exactly the one a reader hovers, and the row used to disappear there.
    const crossover: PnlChartPoint[] = [
      { ...flat[0], position: 800 },
      { ...flat[1], time: 2_000, position: 0 },
      { ...flat[1], time: 3_000, position: -600 },
    ];
    render(<PnlEvolutionChart data={crossover} title="PnL" pnlHeight={220} volumeHeight={120} />);
    hover("activity");
    const [, bottom] = tooltipContents();

    expect(card(bottom, ACTIVITY_KEYS, crossover[1])).toContain(PNL_SERIES_LABELS.position);
  });

  it("drops the position row only when no position series is drawn at all", () => {
    render(<PnlEvolutionChart data={flat} title="PnL" pnlHeight={220} volumeHeight={120} />);
    hover("activity");
    const [, bottom] = tooltipContents();

    expect(card(bottom, ACTIVITY_KEYS, flat[1])).not.toContain(PNL_SERIES_LABELS.position);
  });

  it("colours the PnL rows from the same theme pair as their lines", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);
    hover("pnl");
    const [top] = tooltipContents();
    const html = card(top, PNL_KEYS, withPosition[1]);

    const realizedLine = panes().slice(-2)[0].find((r) => r.type === "Line" && r.props.dataKey === "realized");
    expect(realizedLine?.props.stroke).toBe(getThemeColors().up);
    // Through an element, because the DOM re-serialises an inline hex as rgb().
    const probe = document.createElement("div");
    probe.style.color = getThemeColors().up;
    expect(html).toContain(probe.style.color);
    // Neither this row nor the Total above it goes through `--color-green` any
    // more: the same value as `--chart-up` in every theme shipped today, and a
    // different one the moment a theme parts them.
    expect(html).not.toContain("--color-green");
    expect(html).not.toContain("--color-red");
  });

  it("still gives both panes a Tooltip, so the synced cursor spans both", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);
    expect(recorded.filter((r) => r.type === "Tooltip")).toHaveLength(2);
  });

  it("lets the lower pane's card escape upward, where there is room for it", () => {
    render(<PnlEvolutionChart data={withPosition} title="PnL" pnlHeight={220} volumeHeight={120} />);
    const [top, bottom] = panes().map((pane) => pane.find((r) => r.type === "Tooltip")!);

    // The activity pane is a fraction of the height of the one above it, so a
    // card listing five series does not fit inside it.
    expect(bottom.props.allowEscapeViewBox).toEqual({ x: false, y: true });
    expect(bottom.props.reverseDirection).toEqual({ x: false, y: true });
    // The tall pane needs neither, and escaping there would leave the card.
    expect(top.props.allowEscapeViewBox).toBeUndefined();
    // Both float over the neighbouring pane's SVG.
    for (const t of [top, bottom]) {
      expect((t.props.wrapperStyle as { zIndex: number }).zIndex).toBeGreaterThan(0);
    }
  });
});

describe("PnlEvolutionChart range zoom (READ-249)", () => {
  const HOUR = 3_600_000;
  const t0 = new Date(2026, 2, 14, 8, 0).getTime();

  /** Five hourly points, $100 traded in each bucket after the first. */
  const hourly: PnlChartPoint[] = [0, 1, 2, 3, 4].map((h) => ({
    time: t0 + h * HOUR,
    realized: 10 * h,
    unrealized: 0,
    total: 10 * h,
    volume: 100 * h,
    volumeDelta: h === 0 ? 0 : 100,
    position: 0,
  }));
  /** The same series one socket frame later. */
  const grown: PnlChartPoint[] = [
    ...hourly,
    { ...hourly[4], time: t0 + 5 * HOUR, realized: 50, total: 50, volume: 500, volumeDelta: 100 },
  ];

  const chart = (data: PnlChartPoint[]) => (
    <PnlEvolutionChart data={data} title="Portfolio PnL" pnlHeight={220} volumeHeight={120} />
  );

  /**
   * The strip is drawn in percentages and reads pointers from its live bounding
   * box, which jsdom has no layout to give it — so it gets one. 400px over a
   * four-hour window is 100px an hour, which is what every clientX below means.
   */
  function track(): HTMLElement {
    const el = container.querySelector("[data-range-track]") as HTMLElement;
    expect(el).toBeTruthy();
    el.getBoundingClientRect = () =>
      ({ left: 0, right: 400, width: 400, top: 0, bottom: 24, height: 24, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    return el;
  }

  /** Press on `el`, move the pointer to `toX`, release. */
  function drag(el: Element, toX: number, { release = true } = {}) {
    act(() => {
      el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, clientX: 0 }));
    });
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: toX }));
    });
    if (release) act(() => window.dispatchEvent(new MouseEvent("mouseup", {})));
  }

  const traveller = (end: "start" | "end") =>
    container.querySelector(`[data-range-traveller="${end}"]`) as HTMLElement;

  /**
   * The series each pane was last told to draw. Both `recorded` and `panes()`
   * accumulate across re-renders, so only the last two panes are this render's.
   */
  function drawn(): PnlChartPoint[][] {
    return panes()
      .slice(-2)
      .map((pane) => pane[0].props.data as PnlChartPoint[]);
  }

  /** The header legend row for one series. */
  const legend = (series: string) =>
    container.querySelector(`[data-legend-entry="${series}"]`)!.textContent!;

  it("narrows both panes to the same window when a traveller is dragged", () => {
    render(chart(hourly));
    expect(drawn()[0]).toHaveLength(5);

    // The left traveller pulled to the 2h mark, leaving the right one on the
    // live edge: the window becomes the last two hours.
    track();
    drag(traveller("start"), 200);

    const [top, bottom] = drawn();
    expect(top.map((p) => p.time)).toEqual([t0 + 2 * HOUR, t0 + 3 * HOUR, t0 + 4 * HOUR]);
    // The two panes are separate charts; drawing the same array is the only
    // reason a column in one is the same instant as the column in the other.
    expect(bottom).toBe(top);
  });

  it("rescales both panes' domains to the brushed slice", () => {
    const holding = hourly.map((p, i) => ({ ...p, position: i < 3 ? 5_000 : 100 }));
    render(chart(holding));
    track();
    drag(traveller("start"), 300); // the last hour only

    const [top] = drawn();
    expect(top).toHaveLength(2);
    // recharts derives an auto Y domain from the data it is given, so the PnL
    // pane rescales by being handed the slice...
    expect(top.map((p) => p.total)).toEqual([30, 40]);
    // ...and the position axis, whose domain this component pins itself, is
    // computed from the same slice rather than from the whole window.
    const axis = panes()
      .slice(-2)[1]
      .find((r) => r.type === "YAxis" && r.props.yAxisId === "pos")!;
    expect(axis.props.domain).toEqual(positionAxisDomain(top.slice()));
    const [, max] = axis.props.domain as [number, number];
    expect(max).toBeLessThan(5_000); // the 5,000 held earlier is off screen
  });

  it("reports the volume actually on screen, not the whole loaded window", () => {
    render(chart(hourly));
    expect(legend("volumeDelta")).toContain("$400"); // four buckets of $100

    track();
    drag(traveller("start"), 300);
    // Two of the four buckets left on screen — the figure has to follow the
    // bars beside it, or the header quietly reports a window the chart is not
    // drawing (which is exactly what it did while it read from `data`).
    expect(legend("volumeDelta")).toContain("$200");
    expect(legend("volumeDelta")).toContain("on screen");
  });

  it("keeps a window pinned to the live edge following the new points", () => {
    render(chart(hourly));
    track();
    drag(traveller("start"), 200); // the last two hours, right end on the live edge

    render(chart(grown)); // one socket frame later
    const [top] = drawn();
    // Still two hours wide, and it has slid to cover the point that just landed.
    expect(top.map((p) => p.time)).toEqual([t0 + 3 * HOUR, t0 + 4 * HOUR, t0 + 5 * HOUR]);
  });

  it("leaves a window that does not touch the live edge exactly where it was put", () => {
    render(chart(hourly));
    track();
    drag(traveller("end"), 200); // the *first* two hours

    const before = drawn()[0].map((p) => p.time);
    expect(before).toEqual([t0, t0 + HOUR, t0 + 2 * HOUR]);

    render(chart(grown));
    // A brush that reset itself every time the socket delivered would be worse
    // than no brush: the selection is stored as instants, so it cannot.
    expect(drawn()[0].map((p) => p.time)).toEqual(before);
  });

  it("survives the data being replaced under it by a chip toggle", () => {
    render(chart(hourly));
    track();
    drag(traveller("end"), 200);

    // A controller dropped: the same window, at a different sampling interval,
    // somewhere else entirely on the timeline.
    const elsewhere = hourly.map((p) => ({ ...p, time: p.time + 30 * 86_400_000 }));
    render(chart(elsewhere));
    // No out-of-range slice and no blank pane: the selection cannot be honoured,
    // so the chart is back to the full loaded window.
    expect(drawn()[0]).toHaveLength(elsewhere.length);
  });

  it("zooms to a preset window in one click, and back out again", () => {
    render(chart(hourly));
    const chip = (label: string) => container.querySelector(`[data-range-preset="${label}"]`) as HTMLElement;

    // Only the levels shorter than the four hours loaded are offered.
    expect(chip("1h")).toBeTruthy();
    expect(chip("1d")).toBeNull();
    expect(chip("All").getAttribute("aria-pressed")).toBe("true");

    act(() => chip("1h").dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(drawn()[0].map((p) => p.time)).toEqual([t0 + 3 * HOUR, t0 + 4 * HOUR]);
    expect(chip("1h").getAttribute("aria-pressed")).toBe("true");
    expect(chip("All").getAttribute("aria-pressed")).toBe("false");

    act(() => chip("All").dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(drawn()[0]).toBe(hourly); // the same array, not a copy of it
  });

  it("keeps the hover card out of a drag", () => {
    render(chart(hourly));
    const pane = container.querySelector('[data-pane="pnl"]')!;
    const cardVisible = () =>
      panes()
        .slice(-2)
        .map((p) => {
          const tooltip = p.find((r) => r.type === "Tooltip")!;
          return ((tooltip.props.content as { props: { visible: boolean } }).props.visible);
        });

    act(() => {
      pane.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body }));
    });
    expect(cardVisible()).toEqual([true, false]);

    // Mid-drag the pointer routinely leaves the pane it started in — and a
    // traveller pulled upward crosses the PnL pane, whose enter handler would
    // otherwise pop the card over the window being resized.
    track();
    drag(traveller("start"), 200, { release: false });
    expect(cardVisible()).toEqual([false, false]);

    act(() => window.dispatchEvent(new MouseEvent("mouseup", {})));
    expect(cardVisible()).toEqual([true, false]);
  });

  it("never puts a NaN in the strip's path, whatever the fold hands it", () => {
    // The live series really does carry non-finite totals — a snapshot whose
    // pnl did not arrive as a number folds to one — and the panes answer that
    // with a break in the curve. One NaN in a `d` attribute makes the browser
    // reject the whole path, so the strip would blank itself and log an SVG
    // error on every socket frame instead.
    const broken = hourly.map((p, i) => (i === 3 ? { ...p, total: Number.NaN } : p));
    render(chart(broken));
    const d = container.querySelector("[data-range-track] path")!.getAttribute("d")!;
    expect(d).not.toContain("NaN");
    // The pen lifts over the gap rather than drawing a straight line across it.
    expect((d.match(/M/g) ?? []).length).toBe(2);
  });

  it("offers no strip at all when there is nothing to zoom", () => {
    render(chart(flat)); // two points
    expect(container.querySelector("[data-range-track]")).toBeNull();
  });
});
