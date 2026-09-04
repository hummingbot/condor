/**
 * The scalar candle subscription (PERF-221).
 *
 * `/trade` subscribed the whole candle array to read one number off it —
 * `candles[candles.length - 1].close` — and only on a venue with no REST price,
 * so on every CLOB pair the page re-rendered once a second for an array nothing
 * read. `useLastClose` keeps the number instead of the array. These cases pin
 * the two halves of that: the value still tracks the newest close, and a frame
 * that leaves the close alone costs no render.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleData } from "@/lib/api";

type Listener = (candles: CandleData[]) => void;

/** A stand-in for the singleton store: candles per channel, listeners per channel. */
const store = {
  candles: new Map<string, CandleData[]>(),
  listeners: new Map<string, Set<Listener>>(),
  subscribed: [] as string[],
  unsubscribed: [] as string[],

  subscribe(key: string): CandleData[] {
    store.subscribed.push(key);
    return store.getCandles(key);
  },
  getCandles(key: string): CandleData[] {
    return store.candles.get(key) ?? [];
  },
  unsubscribe(key: string) {
    store.unsubscribed.push(key);
  },
  onUpdate(key: string, cb: Listener): () => void {
    let set = store.listeners.get(key);
    if (!set) {
      set = new Set();
      store.listeners.set(key, set);
    }
    set.add(cb);
    return () => {
      set!.delete(cb);
    };
  },

  /** Push a frame the way the WS does: a brand-new array, every time. */
  push(key: string, candles: CandleData[]) {
    store.candles.set(key, candles);
    for (const cb of store.listeners.get(key) ?? []) cb([...candles]);
  },
  reset() {
    store.candles.clear();
    store.listeners.clear();
    store.subscribed = [];
    store.unsubscribed = [];
  },
};

vi.mock("@/lib/candle-store", () => ({
  candleStore: store,
  candleChannelKey: (
    server: string,
    connector: string,
    pair: string,
    interval: string,
    poolAddress?: string,
  ) => `candles:${server}:${connector}:${pair}:${interval}` + (poolAddress ? `:${poolAddress}` : ""),
}));

const { useLastClose } = await import("./useCandleStore");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function candle(timestamp: number, close: number): CandleData {
  return { timestamp, open: close, high: close, low: close, close, volume: 1 };
}

/** What the harness saw, published from an effect rather than during render. */
const seen = { value: null as number | null, renders: 0 };

function Harness({ server, pair }: { server: string | null; pair: string }) {
  const value = useLastClose(server, "binance", pair, "1m");
  // No dep array: this runs once per commit, so it counts renders and publishes
  // the value from the same place — no mutation during render.
  useEffect(() => {
    seen.renders++;
    seen.value = value;
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;

function render(props: { server: string | null; pair: string }) {
  act(() => {
    root.render(<Harness {...props} />);
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  store.reset();
  seen.value = null;
  seen.renders = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("useLastClose", () => {
  it("returns the newest close and follows it as frames arrive", () => {
    const key = "candles:srv:binance:SOL-USDC:1m";
    store.candles.set(key, [candle(1, 100), candle(2, 101)]);

    render({ server: "srv", pair: "SOL-USDC" });
    expect(seen.value).toBe(101);

    act(() => store.push(key, [candle(1, 100), candle(2, 101), candle(3, 102)]));
    expect(seen.value).toBe(102);

    // A tick that only moves the open candle's close still lands.
    act(() => store.push(key, [candle(1, 100), candle(2, 101), candle(3, 102.5)]));
    expect(seen.value).toBe(102.5);
  });

  it("does not re-render when a frame leaves the close unchanged", () => {
    const key = "candles:srv:binance:SOL-USDC:1m";
    store.candles.set(key, [candle(1, 100)]);

    render({ server: "srv", pair: "SOL-USDC" });
    const before = seen.renders;

    // Three fresh arrays, same last close — the shape of an idle chart.
    act(() => store.push(key, [candle(1, 100)]));
    act(() => store.push(key, [candle(1, 100)]));
    act(() => store.push(key, [candle(1, 100)]));
    expect(seen.renders).toBe(before);
    expect(seen.value).toBe(100);

    // A moved close does render.
    act(() => store.push(key, [candle(1, 100), candle(2, 103)]));
    expect(seen.renders).toBeGreaterThan(before);
    expect(seen.value).toBe(103);
  });

  it("never subscribes when the server is null", () => {
    render({ server: null, pair: "SOL-USDC" });
    expect(seen.value).toBeNull();
    expect(store.subscribed).toEqual([]);
  });

  it("does not leak the previous market's close across a pair change", () => {
    const solKey = "candles:srv:binance:SOL-USDC:1m";
    store.candles.set(solKey, [candle(1, 200)]);

    render({ server: "srv", pair: "SOL-USDC" });
    expect(seen.value).toBe(200);

    // The new pair has no cached candles yet: the price must go blank, not
    // keep quoting SOL.
    render({ server: "srv", pair: "BTC-USDT" });
    expect(seen.value).toBeNull();
    expect(store.unsubscribed).toContain(solKey);

    act(() => store.push("candles:srv:binance:BTC-USDT:1m", [candle(1, 60000)]));
    expect(seen.value).toBe(60000);

    // The old channel is detached — a late frame on it changes nothing.
    act(() => store.push(solKey, [candle(2, 999)]));
    expect(seen.value).toBe(60000);
  });

  it("subscribes once per channel and releases it on unmount", () => {
    render({ server: "srv", pair: "SOL-USDC" });
    expect(store.subscribed).toEqual(["candles:srv:binance:SOL-USDC:1m"]);

    act(() => root.unmount());
    expect(store.unsubscribed).toEqual(["candles:srv:binance:SOL-USDC:1m"]);
    expect(store.listeners.get("candles:srv:binance:SOL-USDC:1m")?.size ?? 0).toBe(0);

    // afterEach unmounts again; a fresh root keeps that harmless.
    root = createRoot(container);
  });
});
