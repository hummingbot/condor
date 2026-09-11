/**
 * That app-load prefetching only warms keys a component can actually read
 * (PERF-335).
 *
 * `usePrefetchData` fires on every app load and again on every server switch,
 * so each request in it is paid for repeatedly. Two used to buy nothing: a
 * 5,000-row candles fetch — the heaviest request the dashboard makes — stored
 * under a `candlesQuery` key whose `endTime` slot is null, which no reader ever
 * asks for (ExecutorChart always passes both bounds; TradeChart doesn't use
 * react-query at all), and `["connectors", server]`, a key no component
 * declares. Both entries were only ever garbage-collected.
 *
 * The assertion is therefore a request count, not a key-shape check: with a
 * server selected, the hook must issue the prefetches that have observers and
 * *no* candles or connectors call. Counting `api` calls rather than the query
 * cache is deliberate — a dead cache entry is invisible, the round trip is not.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";

/** Every `api` member the prefetch hook may reach for, each counted. */
const calls = {
  getExecutors: vi.fn(async () => []),
  getBots: vi.fn(async () => []),
  getConnectedExchanges: vi.fn(async () => ["binance"]),
  getTradingRules: vi.fn(async () => ({})),
  getSettingsServers: vi.fn(async () => []),
  getCredentials: vi.fn(async () => []),
  getAvailableConnectors: vi.fn(async () => []),
  // Kept in the stub on purpose: if the hook ever calls these again, the test
  // must fail on the count, not blow up on an undefined member.
  getCandles: vi.fn(async () => []),
  getConnectors: vi.fn(async () => []),
};

vi.mock("@/lib/api", () => ({ api: calls }));

const { usePrefetchData } = await import("./usePrefetchData");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function Probe() {
  usePrefetchData();
  return null;
}

let container: HTMLDivElement;
let root: Root;

/** Mounts the hook with `server` selected and lets its prefetches settle. */
async function mount(server: string | null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ServerContext.Provider value={{ server, setServer: () => {} }}>
          <Probe />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  // The trading-rules fan-out hangs off a resolved promise, so give the
  // microtask queue a turn before counting.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("usePrefetchData", () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    for (const fn of Object.values(calls)) fn.mockClear();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("spends no request on the candles and connectors keys nothing observes", async () => {
    await mount("alpha");

    expect(calls.getCandles).not.toHaveBeenCalled();
    expect(calls.getConnectors).not.toHaveBeenCalled();
  });

  it("still warms every prefetch that has a real observer", async () => {
    await mount("alpha");

    expect(calls.getExecutors).toHaveBeenCalledTimes(1);
    expect(calls.getBots).toHaveBeenCalledTimes(1);
    expect(calls.getConnectedExchanges).toHaveBeenCalledTimes(1);
    expect(calls.getTradingRules).toHaveBeenCalledWith("alpha", "binance");
    expect(calls.getSettingsServers).toHaveBeenCalledTimes(1);
    expect(calls.getCredentials).toHaveBeenCalledTimes(1);
    // spot + perpetual
    expect(calls.getAvailableConnectors).toHaveBeenCalledTimes(2);
  });

  it("issues eight requests per server, not the ten it used to", async () => {
    await mount("alpha");

    const total = Object.values(calls).reduce((n, fn) => n + fn.mock.calls.length, 0);
    expect(total).toBe(8);
  });

  it("issues nothing at all until a server is selected", async () => {
    await mount(null);

    const total = Object.values(calls).reduce((n, fn) => n + fn.mock.calls.length, 0);
    expect(total).toBe(0);
  });
});
