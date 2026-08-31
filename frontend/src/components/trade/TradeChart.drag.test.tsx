/**
 * Dragging a price line moves the executor's price (FEAT-080).
 *
 * The lines are canvas paint with no mouse API, so the gesture is built beside
 * them: a hit test at pointerdown, `coordinateToPrice(y)` on every coalesced
 * frame, and the existing `onPriceSet` channel as the write-back. These tests
 * drive the real pane handlers and assert the drag emits the pointer's price,
 * suspends panning while it runs, and never lets the drop fall through to a
 * click branch.
 *
 * @vitest-environment jsdom
 */

import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import type { PickSlot } from "@/components/executor/types";
import { TradeChart } from "./TradeChart";

/** A flat, invertible scale: price 700 sits at row 300, price 800 at row 200. */
function priceAtY(y: number): number {
  return 1000 - y;
}
function yAtPrice(price: number): number {
  return 1000 - price;
}

const START_PRICE = 700; // row 300
const END_PRICE = 800; // row 200
const LIMIT_PRICE = 600; // row 400

const chartState = vi.hoisted(() => ({
  series: null as unknown,
  options: {} as Record<string, unknown>,
  primitives: [] as unknown[],
  crosshairCb: null as ((param: unknown) => void) | null,
}));

vi.mock("lightweight-charts", () => {
  const stub = (own: Record<string, unknown>) =>
    new Proxy(own, {
      get(target, prop) {
        if (typeof prop !== "string" || prop === "then") return undefined;
        if (!(prop in target)) target[prop] = vi.fn();
        return target[prop];
      },
    });

  const series = stub({
    coordinateToPrice: vi.fn((y: number) => 1000 - y),
    priceToCoordinate: vi.fn((price: number) => 1000 - price),
    createPriceLine: vi.fn(() => ({})),
    attachPrimitive: vi.fn((p: unknown) => {
      chartState.primitives.push(p);
      (p as { attached?: (a: unknown) => void }).attached?.({ series });
    }),
    detachPrimitive: vi.fn((p: unknown) => {
      chartState.primitives = chartState.primitives.filter((x) => x !== p);
      (p as { detached?: () => void }).detached?.();
    }),
  });
  chartState.series = series;

  const timeScale = stub({});
  const chart = stub({
    addSeries: vi.fn(() => series),
    timeScale: vi.fn(() => timeScale),
    subscribeCrosshairMove: vi.fn((cb: (param: unknown) => void) => {
      chartState.crosshairCb = cb;
    }),
    applyOptions: vi.fn((opts: Record<string, unknown>) => {
      Object.assign(chartState.options, opts);
    }),
  });

  return {
    createChart: vi.fn(() => chart),
    CandlestickSeries: {},
    LineSeries: {},
    ColorType: { Solid: "solid" },
    CrosshairMode: { Normal: 0 },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  };
});

vi.mock("@/hooks/useCandleStore", () => ({
  useCandleStore: () => ({
    candles: [],
    isStale: false,
    mergeCandles: vi.fn(),
    setDuration: vi.fn(),
  }),
}));

vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    formatPnlValue: (v: number) => String(v),
    formatValue: (v: number) => String(v),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: { getCandles: vi.fn(async () => []) },
}));

vi.mock("@/lib/candle-store", () => ({
  candleChannelKey: (...parts: unknown[]) => parts.join(":"),
  candleStore: { onUpdate: vi.fn(() => () => {}) },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let onPriceSet: Mock<(field: PickSlot, price: number) => void>;

async function render(extra: Partial<ComponentProps<typeof TradeChart>> = {}) {
  await act(async () => {
    root.render(
      <TradeChart
        server="local"
        connector="binance"
        pair="BTC-USDT"
        interval="1m"
        lookbackSeconds={3600}
        startPrice={START_PRICE}
        endPrice={END_PRICE}
        limitPrice={LIMIT_PRICE}
        side={1}
        minSpread={0.001}
        activePickField={null}
        onPriceSet={onPriceSet}
        pricePrecision={2}
        {...extra}
      />,
    );
  });
}

/** The pane the pointer handlers are bound to. */
function pane(): HTMLDivElement {
  return container.querySelector(".absolute.inset-0") as HTMLDivElement;
}

/**
 * jsdom has no `PointerEvent`, but React dispatches by event type — a
 * `MouseEvent` named `pointerdown` reaches the same handler with the
 * `clientY` / `button` / `shiftKey` the gesture reads. `getBoundingClientRect`
 * answers zeros in jsdom, so `clientY` *is* the pane row.
 */
function fire(type: string, y: number, opts: { shiftKey?: boolean; button?: number } = {}) {
  pane().dispatchEvent(
    new MouseEvent(type, {
      bubbles: true,
      button: opts.button ?? 0,
      shiftKey: opts.shiftKey ?? false,
      clientX: 300,
      clientY: y,
    }),
  );
}

/** Press on `fromY`, travel through `throughY`, release on the last of them. */
async function drag(fromY: number, ...throughY: number[]) {
  await act(async () => {
    fire("pointerdown", fromY);
    for (const y of throughY) fire("pointermove", y);
    fire("pointerup", throughY.length ? throughY[throughY.length - 1] : fromY);
  });
}

/** Park the crosshair on a pixel row, which is what the click branches read. */
async function hoverAt(y: number) {
  await act(async () => {
    chartState.crosshairCb?.({
      point: { x: 300, y },
      seriesData: new Map(),
      time: 1_700_000_000,
    });
  });
}

/** Let a coalescing animation frame run. */
async function nextFrame() {
  await act(async () => {
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  chartState.options = {};
  chartState.primitives = [];
  chartState.crosshairCb = null;
  onPriceSet = vi.fn<(field: PickSlot, price: number) => void>();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("TradeChart price-line drag", () => {
  it("moves the grabbed line's price to the pointer", async () => {
    await render();
    await drag(yAtPrice(START_PRICE), 340);

    expect(onPriceSet).toHaveBeenCalledWith("start", priceAtY(340));
    for (const [slot] of onPriceSet.mock.calls) expect(slot).toBe("start");
  });

  it("grabs whichever line the press landed on", async () => {
    await render();
    await drag(yAtPrice(END_PRICE), 180);
    expect(onPriceSet.mock.calls[0][0]).toBe("end");

    onPriceSet.mockClear();
    await drag(yAtPrice(LIMIT_PRICE), 430);
    expect(onPriceSet.mock.calls[0][0]).toBe("limit");
  });

  it("rounds the reported price to the venue's precision", async () => {
    await render({ pricePrecision: 1 });
    // Row 340.44 maps to 659.56, which precision 1 rounds to 659.6.
    await drag(yAtPrice(START_PRICE), 340.44);

    expect(onPriceSet).toHaveBeenCalledWith("start", 659.6);
  });

  it("reports the moves live, coalesced to one call per frame", async () => {
    await render();
    await act(async () => {
      fire("pointerdown", yAtPrice(START_PRICE));
      fire("pointermove", 310);
      fire("pointermove", 320);
      fire("pointermove", 330);
    });
    await nextFrame();

    // Three moves inside one frame report once, at the latest position...
    expect(onPriceSet).toHaveBeenCalledTimes(1);
    expect(onPriceSet).toHaveBeenLastCalledWith("start", priceAtY(330));

    // ...and the drag is still live: a later frame reports again.
    await act(async () => { fire("pointermove", 350); });
    await nextFrame();
    expect(onPriceSet).toHaveBeenCalledTimes(2);
    expect(onPriceSet).toHaveBeenLastCalledWith("start", priceAtY(350));
  });

  it("lands the final position even when the release interrupts a frame", async () => {
    await render();
    await act(async () => {
      fire("pointerdown", yAtPrice(START_PRICE));
      fire("pointermove", 310);
      fire("pointerup", 355);
    });

    expect(onPriceSet).toHaveBeenLastCalledWith("start", priceAtY(355));
  });

  it("leaves the pointer alone away from every line", async () => {
    await render();
    await drag(150, 160);

    expect(onPriceSet).not.toHaveBeenCalled();
  });

  it("keeps shift for the measure tool", async () => {
    await render();
    await act(async () => {
      fire("pointerdown", yAtPrice(START_PRICE), { shiftKey: true });
      fire("pointermove", 340);
      fire("pointerup", 340);
    });

    expect(onPriceSet).not.toHaveBeenCalled();
  });

  it("ignores a press that is not the left button", async () => {
    await render();
    await act(async () => {
      fire("pointerdown", yAtPrice(START_PRICE), { button: 2 });
      fire("pointermove", 340);
      fire("pointerup", 340, { button: 2 });
    });

    expect(onPriceSet).not.toHaveBeenCalled();
  });

  it("suspends chart panning for the drag and hands it back on release", async () => {
    await render();
    await act(async () => {
      fire("pointerdown", yAtPrice(START_PRICE));
      fire("pointermove", 340);
    });
    expect(chartState.options).toMatchObject({ handleScroll: false, handleScale: false });

    await act(async () => { fire("pointerup", 340); });
    expect(chartState.options).toMatchObject({ handleScroll: true, handleScale: true });
  });

  it("hands panning back when the component unmounts mid-drag", async () => {
    await render();
    await act(async () => {
      fire("pointerdown", yAtPrice(START_PRICE));
      fire("pointermove", 340);
    });
    expect(chartState.options).toMatchObject({ handleScroll: false });

    await act(async () => { root.unmount(); });
    expect(chartState.options).toMatchObject({ handleScroll: true, handleScale: true });

    // The afterEach unmount must stay harmless.
    root = createRoot(container);
  });

  it("does not pick or deselect on the drop that ends a drag", async () => {
    const onExecutorDeselect = vi.fn();
    await render({ activePickField: "start", selectedExecutorId: "exec-1", onExecutorDeselect });
    await drag(yAtPrice(START_PRICE), 340);

    expect(onExecutorDeselect).not.toHaveBeenCalled();
    // Every report came from the drag, at the pointer — none from the pick branch.
    expect(onPriceSet).toHaveBeenLastCalledWith("start", priceAtY(340));
  });

  it("lets an armed pick answer a stationary click on a line", async () => {
    // A press that never travels is a tap, whatever it landed on: the grab
    // stands down and pick mode gets the click it was armed for.
    await render({ activePickField: "end" });
    await hoverAt(yAtPrice(START_PRICE));
    await act(async () => {
      fire("pointerdown", yAtPrice(START_PRICE));
      fire("pointerup", yAtPrice(START_PRICE));
    });

    expect(onPriceSet).toHaveBeenCalledTimes(1);
    expect(onPriceSet).toHaveBeenCalledWith("end", START_PRICE);
    // And the press handed panning straight back.
    expect(chartState.options).toMatchObject({ handleScroll: true, handleScale: true });
  });

  it("writes nothing for a wiggle that stays inside the click slop", async () => {
    await render({ activePickField: null, selectedExecutorId: "exec-1" });
    await act(async () => {
      fire("pointerdown", yAtPrice(START_PRICE));
      fire("pointermove", yAtPrice(START_PRICE) + 2);
      fire("pointerup", yAtPrice(START_PRICE) + 2);
    });
    await nextFrame();

    expect(onPriceSet).not.toHaveBeenCalled();
  });

  it("drags a panel's extra line when it declares a slot", async () => {
    await render({
      extraLines: [
        {
          price: 650,
          label: "Lower limit",
          color: "#f00",
          lineStyle: "dotted",
          lineWidth: 1,
          slot: "limit2",
        },
      ],
    });
    await drag(yAtPrice(650), 360);

    expect(onPriceSet).toHaveBeenLastCalledWith("limit2", priceAtY(360));
  });

  it("leaves a decorative extra line inert", async () => {
    await render({
      extraLines: [
        { price: 650, label: "Mid", color: "#f00", lineStyle: "dotted", lineWidth: 1 },
      ],
    });
    await drag(yAtPrice(650), 360);

    expect(onPriceSet).not.toHaveBeenCalled();
  });
});

describe("TradeChart drag hover cursor", () => {
  it("asks for a resize cursor over a draggable line and nothing elsewhere", async () => {
    await render();
    const primitive = chartState.primitives[0] as {
      hitTest?: (x: number, y: number) => { cursorStyle?: string; externalId: string } | null;
    };
    expect(primitive).toBeTruthy();

    expect(primitive.hitTest?.(300, yAtPrice(START_PRICE))).toMatchObject({
      cursorStyle: "ns-resize",
      externalId: "drag:start",
    });
    expect(primitive.hitTest?.(300, 150)).toBeNull();
  });
});
