/**
 * The run's Detail band reads a finished run's executors once (PERF-384).
 *
 * RunOverview and the SessionExecutors it mounts both observe
 * `["strategy-session-executors", slug, sslug, n]`, and react-query polls a
 * shared key at the shortest interval among its observers — so both have to
 * gate, and this mounts them together to prove the pair is quiet.
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
    getSessionJournal: vi.fn(async () => ({ content: "" })),
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
vi.mock("@/components/agent/SessionActions", () => ({
  SessionActions: () => null,
}));
vi.mock("@/components/agent/session/SessionCanvasPanel", () => ({
  SessionCanvasPanel: () => null,
}));
vi.mock("@/components/agent/session/SessionBots", () => ({
  SessionBots: () => null,
}));

const { RunOverview } = await import("./RunOverview");

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
          <RunOverview
            slug="brigado"
            sslug="brl_mm"
            sessionNum={3}
            serverName="local"
            isLiveSession={isLiveSession}
            onSelectTick={() => {}}
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
  getStrategySessionExecutors
    .mockReset()
    .mockResolvedValue({ executors: [], performance: null, deployments: [] });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("RunOverview executors polling (PERF-384)", () => {
  it("reads a finished run's executors exactly once across a minute", async () => {
    await mount(false);
    await advance(60_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(1);
  });

  it("still re-reads a live run every 10 s", async () => {
    await mount(true);
    await advance(60_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(7);
  });
});
