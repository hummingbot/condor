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

import type { AgentRunRow } from "@/lib/api";

const getSessionJournal = vi.fn();
const getSessionActions = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getSessionJournal: (...a: unknown[]) => getSessionJournal(...a),
    getSessionActions: (...a: unknown[]) => getSessionActions(...a),
    getStrategySessionExecutors: () =>
      Promise.resolve({ deployments: [], performance: null, pnl_series: null }),
  },
}));

const { useWorkspaceAlerts } = await import("./useWorkspaceAlerts");

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

function Probe({ status }: { status: string }) {
  useWorkspaceAlerts({
    slug: "brigado",
    sslug: "brl_mm",
    run: run(status),
    instance: null,
  });
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
  getSessionJournal.mockReset().mockResolvedValue({ content: "" });
  getSessionActions.mockReset().mockResolvedValue({ actions: [] });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("useWorkspaceAlerts polling", () => {
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
});
