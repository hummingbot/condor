/**
 * A finished session's executors are read once, not every 10 s (PERF-384).
 *
 * `GET .../sessions/{n}/executors` has no server cache: every call walks
 * Hummingbot's executor search and the bots' history. A stopped session owns no
 * bot and its record no longer moves, yet the table re-read it six times a
 * minute for as long as it stayed open.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const getStrategySessionExecutors = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: {
    getStrategySessionExecutors: (...a: unknown[]) => getStrategySessionExecutors(...a),
    getSessionSnapshots: vi.fn(async () => ({ snapshots: [] })),
    getPositionsHeld: vi.fn(async () => ({ positions: [], summary: {} })),
  },
}));
vi.mock("@/hooks/useAgentExecutors", () => ({
  useAgentExecutors: () => ({ executors: [] }),
}));
vi.mock("@/hooks/useSnapshotBubbles", () => ({
  useSnapshotBubbles: () => [],
}));
vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    rates: {},
    currency: "USD",
    formatPnlValue: (v: number) => String(v),
    formatValue: (v: number) => String(v),
    formatValueDetailed: (v: number) => String(v),
  }),
}));
vi.mock("@/components/charts/ExecutorChart", () => ({
  ExecutorChart: () => null,
}));

const { SessionExecutors } = await import("./SessionExecutors");

let container: HTMLDivElement;
let root: Root;

async function mount(isLiveSession: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SessionExecutors
            slug="brigado"
            sslug="brl_mm"
            sessionNum={3}
            serverName="local"
            isLiveSession={isLiveSession}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  getStrategySessionExecutors.mockReset().mockResolvedValue({ executors: [] });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("SessionExecutors polling (PERF-384)", () => {
  it("reads a finished session once across a minute", async () => {
    await mount(false);
    await advance(60_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(1);
    expect(getStrategySessionExecutors).toHaveBeenCalledWith("brigado", "brl_mm", 3);
  });

  it("re-reads the live session every 10 s", async () => {
    await mount(true);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(1);
    await advance(60_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(7);
  });

  it("stops polling once the session is no longer live", async () => {
    await mount(true);
    await advance(10_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(2);

    await mount(false);
    const settled = getStrategySessionExecutors.mock.calls.length;
    await advance(60_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(settled);
  });
});
