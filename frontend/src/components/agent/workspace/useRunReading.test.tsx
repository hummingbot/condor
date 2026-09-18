/**
 * A live run's journal and action log are re-read on their own (CORR-369).
 *
 * Nothing pushes either: no socket frame, no invalidation on a tick. Before
 * this, the last decision, the failed-action alert and the tick spine showed
 * the run as it was at page open while the countdown next to them kept moving.
 * The poll is gated on the run being live, so a finished run costs nothing.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkspaceAlert } from "@/components/agent/workspace/views";
import type { AgentRunRow } from "@/lib/api";

const getSessionJournal = vi.fn();
const getSessionActions = vi.fn();
const getStrategySessionExecutors = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getSessionJournal: (...a: unknown[]) => getSessionJournal(...a),
    getSessionActions: (...a: unknown[]) => getSessionActions(...a),
    getStrategySessionExecutors: (...a: unknown[]) => getStrategySessionExecutors(...a),
  },
}));

const { useRunReading } = await import("./useRunReading");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const run = (status: string) =>
  ({
    id: "session_3",
    run_id: "s:3",
    kind: "session",
    number: 3,
    strategy_slug: "brl_mm",
    status,
  }) as unknown as AgentRunRow;

/** Every `alerts` the hook returned, one per render of the probe. */
let seen: WorkspaceAlert[][] = [];

function Probe({ status }: { status: string }) {
  const { alerts } = useRunReading({
    slug: "brigado",
    sslug: "brl_mm",
    run: run(status),
  });
  seen.push(alerts);
  return null;
}

let container: HTMLDivElement;
let root: Root;

async function mount(status: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Probe status={status} />
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
  seen = [];
  getSessionJournal.mockReset().mockResolvedValue({ content: "" });
  getSessionActions.mockReset().mockResolvedValue({ actions: [] });
  getStrategySessionExecutors
    .mockReset()
    .mockResolvedValue({ deployments: [], performance: null, pnl_series: null });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("useRunReading polling", () => {
  it("re-reads a running run's journal and action log every 10s", async () => {
    await mount("running");
    expect(getSessionJournal).toHaveBeenCalledTimes(1);
    expect(getSessionActions).toHaveBeenCalledTimes(1);

    await advance(10_000);
    expect(getSessionJournal).toHaveBeenCalledTimes(2);
    expect(getSessionActions).toHaveBeenCalledTimes(2);
  });

  it("polls a paused run too — it can still be resumed under the reader", async () => {
    await mount("paused");
    await advance(10_000);
    expect(getSessionJournal).toHaveBeenCalledTimes(2);
    expect(getSessionActions).toHaveBeenCalledTimes(2);
  });

  it("reads a stopped run once and never again", async () => {
    await mount("stopped");
    await advance(30_000);
    expect(getSessionJournal).toHaveBeenCalledTimes(1);
    expect(getSessionActions).toHaveBeenCalledTimes(1);
  });

  it("re-reads a live run's executors every 10s and a stopped run's once (PERF-384)", async () => {
    await mount("running");
    await advance(10_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(2);

    act(() => root.unmount());
    root = createRoot(container);
    getStrategySessionExecutors.mockClear();
    await mount("stopped");
    await advance(60_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(1);
  });
});

describe("the run screen raises no overdue alert (READ-424)", () => {
  // The loop bar above the tabs already counts a late tick in amber and never
  // unmounts, so the hook no longer reads the live engine at all: no overdue
  // banner, no Now badge, and no once-a-second clock to keep one current. Fleet
  // rows, which have no loop bar, keep the rule — see fleet.test.ts.

  // The 1 s intervals started. Not `vi.getTimerCount()`: react-query's own 10 s
  // poll is a timer too.
  let clocks: number;
  beforeEach(() => {
    clocks = 0;
    const set = globalThis.setInterval;
    vi.spyOn(globalThis, "setInterval").mockImplementation(((
      fn: () => void,
      ms?: number,
    ) => {
      if (ms === 1000) clocks += 1;
      return set(fn, ms);
    }) as typeof setInterval);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("runs no per-second clock and re-renders nothing between polls", async () => {
    await mount("running");
    const renders = seen.length;
    const alerts = seen.at(-1);

    for (let i = 0; i < 5; i++) await advance(1_000);
    expect(clocks).toBe(0);
    expect(seen.length).toBe(renders);
    expect(seen.at(-1)).toBe(alerts);
    expect(alerts).toEqual([]);
  });
});
