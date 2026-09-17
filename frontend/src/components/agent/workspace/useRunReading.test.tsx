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
import type { AgentRunRow, RunningInstance } from "@/lib/api";

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

function Probe({
  status,
  instance = null,
}: {
  status: string;
  instance?: RunningInstance | null;
}) {
  const { alerts } = useRunReading({
    slug: "brigado",
    sslug: "brl_mm",
    run: run(status),
    instance,
  });
  seen.push(alerts);
  return null;
}

let container: HTMLDivElement;
let root: Root;

async function mount(status: string, instance: RunningInstance | null = null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Probe status={status} instance={instance} />
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

  it("does not poll a finished run's executors while another session's engine runs", async () => {
    const other = { agent_id: "a1", status: "running" } as unknown as RunningInstance;
    await mount("stopped", other);
    await advance(60_000);
    expect(getStrategySessionExecutors).toHaveBeenCalledTimes(1);
  });
});

describe("the overdue clock (PERF-372)", () => {
  /** A loop whose next tick fell due `lateSec` seconds ago (negative: still ahead). */
  const loop = (lateSec: number, status = "running") =>
    ({
      agent_id: "a1",
      status,
      frequency_sec: 60,
      last_tick_at: Date.now() / 1000 - 60 - lateSec,
    }) as unknown as RunningInstance;
  const overdue = () =>
    seen.at(-1)!.filter((a) => a.kind === "overdue").map((a) => a.text);

  // The 1 s intervals still running. Not `vi.getTimerCount()`: react-query's
  // own 10 s poll is a timer too, and it is not the clock under test.
  let clocks: Set<unknown>;
  beforeEach(() => {
    clocks = new Set();
    const set = globalThis.setInterval;
    const clear = globalThis.clearInterval;
    vi.spyOn(globalThis, "setInterval").mockImplementation(((
      fn: () => void,
      ms?: number,
    ) => {
      const id = set(fn, ms);
      if (ms === 1000) clocks.add(id);
      return id;
    }) as typeof setInterval);
    vi.spyOn(globalThis, "clearInterval").mockImplementation(((
      id?: Parameters<typeof clearInterval>[0],
    ) => {
      clocks.delete(id);
      clear(id);
    }) as typeof clearInterval);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("counts a late tick up once a second", async () => {
    await mount("running", loop(2));
    expect(overdue()).toEqual(["The next tick is 2s overdue."]);

    await advance(1_000);
    expect(overdue()).toEqual(["The next tick is 3s overdue."]);
  });

  it("keeps the first second of lateness", async () => {
    await mount("running", loop(0.5));
    expect(overdue()).toEqual(["The next tick is 0s overdue."]);
  });

  it("does not re-render a loop that is on time", async () => {
    await mount("running", loop(-30));
    const renders = seen.length;
    const alerts = seen.at(-1);

    for (let i = 0; i < 5; i++) await advance(1_000);
    expect(seen.length).toBe(renders);
    expect(seen.at(-1)).toBe(alerts);
    expect(overdue()).toEqual([]);
  });

  it.each([
    ["no loop", null],
    ["a loop that is not running", loop(120, "stopped")],
  ])("holds the same alerts with %s, and runs no clock", async (_, instance) => {
    await mount("stopped", instance);
    const alerts = seen.at(-1);
    await advance(5_000);
    expect(seen.at(-1)).toBe(alerts);
    expect(overdue()).toEqual([]);
    expect(clocks.size).toBe(0);
  });

  it("leaves no interval behind once unmounted", async () => {
    await mount("stopped", loop(2));
    expect(clocks.size).toBe(1);
    act(() => root.unmount());
    expect(clocks.size).toBe(0);
    root = createRoot(container);
  });
});
