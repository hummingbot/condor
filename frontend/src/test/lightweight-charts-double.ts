/**
 * The only `lightweight-charts` any test ever gets (CORR-368).
 *
 * Every runtime resolution of the library in `src/` is a *dynamic* `import()`
 * (`TradeChart`, `ExecutorChart`, `AgentPnlChart`). When two chart mounts land
 * in one `act()` — a `StrictMode` double mount, a detail panel beside a fleet
 * chart, two roots in one test — the module is requested twice concurrently,
 * and Vitest's mocker answers a per-file `vi.mock` stub to the first request
 * while raw-importing the real library for the second. The real `ChartWidget`
 * then builds against a jsdom that has no 2D canvas and schedules a draw frame
 * that fires after teardown, outside every test: the run exits 1 with unhandled
 * errors while every individual test reports green (CORR-360 fixed one file
 * defensively; this replaces that whole class of accident).
 *
 * So the substitution happens at the resolver instead: `vite.config.ts` maps
 * `lightweight-charts` to this file for the whole test run, so *every*
 * resolution — including the losing concurrent one — lands here. A test file
 * cannot reach the real library even by forgetting to mock it, and a new chart
 * test gets the double for free. `lightweight-charts-double.test.tsx` holds
 * that wiring to its promise.
 *
 * Tests reach the recorded state through `chartDouble`, importing it by path
 * (`@/test/lightweight-charts-double`) so TypeScript still checks the
 * production code against the library's real types.
 */

import { vi } from "vitest";

type Dict = Record<string, unknown>;

/**
 * Unknown members answer with a no-op spy, so a chart can walk any shape the
 * component reaches for; only the members below carry real behaviour. `then`
 * is excluded because these objects travel through promise chains and a
 * callable `then` would make one look thenable.
 */
function stub(own: Dict): Dict {
  return new Proxy(own, {
    get(target, prop) {
      if (typeof prop !== "string" || prop === "then") return undefined;
      if (!(prop in target)) target[prop] = vi.fn();
      return target[prop];
    },
  });
}

/**
 * The pixel↔price mapping a test wants. Charts are canvas paint with no
 * geometry in jsdom, so the double invents one and each test declares the scale
 * its assertions need via `chartDouble.reset()`.
 */
export interface PriceScale {
  /** Pixel row → price, what `series.coordinateToPrice` answers. */
  toPrice: (y: number) => number;
  /** Price → pixel row, what `series.priceToCoordinate` answers. */
  toCoordinate: (price: number) => number;
}

const FLAT_SCALE: PriceScale = { toPrice: () => 0, toCoordinate: () => 0 };
let scale: PriceScale = { ...FLAT_SCALE };

/** One created chart and everything the component did to it. */
interface ChartRecord {
  /** The element `createChart` was handed. */
  container: unknown;
  /** The options `createChart` was handed — *not* merged into `options`. */
  initialOptions: Dict;
  chart: Dict;
  /** One series object shared by every `addSeries` call on this chart. */
  series: Dict;
  timeScale: Dict;
  /** The series-type sentinel of each `addSeries` call, in order. */
  addedSeries: unknown[];
  removedSeries: unknown[];
  /** Everything `chart.applyOptions` has merged in, latest write winning. */
  options: Dict;
  /** Primitives currently attached to the series. */
  primitives: unknown[];
  crosshairHandlers: ((param: unknown) => void)[];
}

const charts: ChartRecord[] = [];

const last = (): ChartRecord | undefined => charts[charts.length - 1];

// ── The module surface the components import ────────────────────────────────

/** Series-type sentinels: identity is all the components and the double use. */
export const AreaSeries = { seriesType: "Area" };
export const BarSeries = { seriesType: "Bar" };
export const BaselineSeries = { seriesType: "Baseline" };
export const CandlestickSeries = { seriesType: "Candlestick" };
export const HistogramSeries = { seriesType: "Histogram" };
export const LineSeries = { seriesType: "Line" };

export const ColorType = { Solid: "solid", VerticalGradient: "gradient" };
export const CrosshairMode = { Normal: 0, Magnet: 1, Hidden: 2 };
export const LineStyle = {
  Solid: 0,
  Dotted: 1,
  Dashed: 2,
  LargeDashed: 3,
  SparseDotted: 4,
};
export const LineType = { Simple: 0, WithSteps: 1, Curved: 2 };
export const PriceScaleMode = { Normal: 0, Logarithmic: 1 };
export const TickMarkType = {
  Year: 0,
  Month: 1,
  DayOfMonth: 2,
  Time: 3,
  TimeWithSeconds: 4,
};

export const createChart = vi.fn((container: unknown, initialOptions: Dict = {}) => {
  const record: ChartRecord = {
    container,
    initialOptions,
    chart: {},
    series: {},
    timeScale: {},
    addedSeries: [],
    removedSeries: [],
    options: {},
    primitives: [],
    crosshairHandlers: [],
  };

  const series = stub({
    coordinateToPrice: vi.fn((y: number) => scale.toPrice(y)),
    priceToCoordinate: vi.fn((price: number) => scale.toCoordinate(price)),
    createPriceLine: vi.fn(() => ({})),
    attachPrimitive: vi.fn((primitive: unknown) => {
      record.primitives.push(primitive);
      (primitive as { attached?: (param: unknown) => void }).attached?.({ series });
    }),
    detachPrimitive: vi.fn((primitive: unknown) => {
      record.primitives = record.primitives.filter((p) => p !== primitive);
      (primitive as { detached?: () => void }).detached?.();
    }),
  });

  const timeScale = stub({ scrollPosition: vi.fn(() => 0) });

  const chart = stub({
    // Real charts mint a series per call; one shared object per chart is enough
    // here and lets a test read `chartDouble.series` without caring which of a
    // component's several series it got.
    addSeries: vi.fn((seriesType: unknown) => {
      record.addedSeries.push(seriesType);
      return series;
    }),
    removeSeries: vi.fn((removed: unknown) => {
      record.removedSeries.push(removed);
    }),
    timeScale: vi.fn(() => timeScale),
    applyOptions: vi.fn((options: Dict) => {
      Object.assign(record.options, options);
    }),
    subscribeCrosshairMove: vi.fn((cb: (param: unknown) => void) => {
      record.crosshairHandlers.push(cb);
    }),
    unsubscribeCrosshairMove: vi.fn((cb: (param: unknown) => void) => {
      record.crosshairHandlers = record.crosshairHandlers.filter((h) => h !== cb);
    }),
  });

  record.chart = chart;
  record.series = series;
  record.timeScale = timeScale;
  charts.push(record);
  return chart;
});

// ── The handle tests read ───────────────────────────────────────────────────

export const chartDouble = {
  /** Every chart created since the last `reset()`, in creation order. */
  get charts(): readonly ChartRecord[] {
    return charts;
  },
  /** The most recently created chart, or `null` before anything mounted. */
  get chart(): Dict | null {
    return last()?.chart ?? null;
  },
  /** The most recent chart's series — the object its `addSeries` handed back. */
  get series(): Dict | null {
    return last()?.series ?? null;
  },
  get timeScale(): Dict | null {
    return last()?.timeScale ?? null;
  },
  /** What the most recent chart's `applyOptions` calls have merged in. */
  get options(): Dict {
    return last()?.options ?? {};
  },
  /** Primitives currently attached to the most recent chart's series. */
  get primitives(): readonly unknown[] {
    return last()?.primitives ?? [];
  },
  /** The last crosshair handler the most recent chart subscribed. */
  get crosshairCb(): ((param: unknown) => void) | null {
    return last()?.crosshairHandlers.at(-1) ?? null;
  },
  /** Line series added across every chart — the overlay churn under test. */
  get lineSeriesAdded(): number {
    return charts.reduce(
      (n, c) => n + c.addedSeries.filter((type) => type === LineSeries).length,
      0,
    );
  },
  /** Series removed across every chart. */
  get seriesRemoved(): number {
    return charts.reduce((n, c) => n + c.removedSeries.length, 0);
  },

  /**
   * Forget every chart and reinstate the price scale. Call it in `beforeEach`,
   * passing the mapping this file's assertions read prices off.
   */
  reset(priceScale: Partial<PriceScale> = {}): void {
    charts.length = 0;
    createChart.mockClear();
    scale = { ...FLAT_SCALE, ...priceScale };
  },
};
