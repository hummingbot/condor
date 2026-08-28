/**
 * FEAT-076: the dashboard submits the routine, not the task API.
 *
 * There is one thing that runs a backtest in Condor — the `backtest_chart`
 * routine — and the tab is one of its callers. What that buys is pinned here:
 * a run is launched as a routine (quietly, with the form's own dates), a run
 * launched from anywhere else shows up as in-flight, and a finished run hands
 * over to its archive entry through the `task_id` in its metrics row.
 *
 * The handover seam is read by key, never by position: `METRIC_COLUMNS` is
 * FEAT-039's agent-facing contract and the dashboard is now one of its readers.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Args = unknown[];

const getAvailableConfigs = vi.fn(async (..._a: Args) => ({ configs: [] }));
const getRoutineInstances = vi.fn(async (..._a: Args) => [] as unknown[]);
const getRoutineInstance = vi.fn(async (..._a: Args) => null as unknown);
const listBacktestArchive = vi.fn(async (..._a: Args) => ({
  migrated: true,
  summaries: [] as unknown[],
}));
const getArchivedBacktest = vi.fn(async (..._a: Args) => null as unknown);
const runRoutine = vi.fn(async (..._a: Args) => ({ instance_id: "inst-1" }));
const stopRoutineInstance = vi.fn(async (..._a: Args) => ({ stopped: true }));
const deleteArchivedBacktest = vi.fn(async (..._a: Args) => ({}));

vi.mock("@/lib/api", () => ({
  api: {
    getAvailableConfigs: (...a: Args) => getAvailableConfigs(...a),
    getRoutineInstances: (...a: Args) => getRoutineInstances(...a),
    getRoutineInstance: (...a: Args) => getRoutineInstance(...a),
    listBacktestArchive: (...a: Args) => listBacktestArchive(...a),
    getArchivedBacktest: (...a: Args) => getArchivedBacktest(...a),
    runRoutine: (...a: Args) => runRoutine(...a),
    stopRoutineInstance: (...a: Args) => stopRoutineInstance(...a),
    deleteArchivedBacktest: (...a: Args) => deleteArchivedBacktest(...a),
  },
}));

const { useBacktest, taskIdFromInstance } = await import("./useBacktest");

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

type Hook = ReturnType<typeof useBacktest>;

let container: HTMLDivElement;
let root: Root;
let hook: Hook;

function Probe({ server }: { server: string | null }) {
  const value = useBacktest(server);
  useEffect(() => {
    hook = value;
  });
  hook = value;
  return null;
}

async function render(server: string | null = "brigado_2") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Probe server={server} />
      </QueryClientProvider>,
    );
  });
  await flush();
}

const flush = () =>
  act(async () => void (await new Promise((r) => setTimeout(r, 0))));

function instance(over: Record<string, unknown> = {}) {
  return {
    instance_id: "inst-1",
    routine_name: "backtest_chart",
    status: "running",
    source: "web",
    server_name: "brigado_2",
    config: {
      config_name: "pmm-sol",
      start_date: "2026-08-01",
      end_date: "2026-08-08",
      resolution: "1m",
      trade_cost: 0.0002,
      chart: false,
    },
    created_at: 1_756_000_000,
    last_run_at: null,
    last_result: null,
    last_duration: null,
    run_count: 0,
    error: null,
    ...over,
  };
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.clearAllMocks();
  getAvailableConfigs.mockResolvedValue({ configs: [] });
  getRoutineInstances.mockResolvedValue([]);
  listBacktestArchive.mockResolvedValue({ migrated: true, summaries: [] });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("submitting", () => {
  it("runs the backtest routine, quietly, with the form's own dates", async () => {
    await render();

    await act(async () => {
      hook.submit.mutate({
        config_id: "pmm-sol",
        start_date: "2026-08-01",
        end_date: "2026-08-08",
        backtesting_resolution: "1m",
        trade_cost: 0.0002,
      });
    });
    await flush();

    expect(runRoutine).toHaveBeenCalledWith("brigado_2", "backtest_chart", {
      config_name: "pmm-sol",
      start_date: "2026-08-01",
      end_date: "2026-08-08",
      resolution: "1m",
      trade_cost: 0.0002,
      // The tab draws its own chart: no Telegram photo, no PNG cost.
      chart: false,
    });
  });

  it("selects the launched run before any list has heard of it", async () => {
    await render();

    await act(async () => {
      hook.submit.mutate({
        config_id: "pmm-sol",
        start_date: "2026-08-01",
        end_date: "2026-08-08",
      });
    });
    await flush();

    expect(hook.selectedTaskId).toBe("inst-1");
    // And it is not asked for in the archive: it is not a task id yet.
    expect(getArchivedBacktest).not.toHaveBeenCalled();
  });
});

describe("in-flight runs", () => {
  it("shows a run nobody launched from this browser", async () => {
    getRoutineInstances.mockResolvedValue([instance()]);

    await render();

    expect(hook.tasks).toHaveLength(1);
    expect(hook.tasks[0]).toMatchObject({
      task_id: "inst-1",
      instance_id: "inst-1",
      status: "running",
      server: "brigado_2",
    });
    // The row renders the run's parameters like any other.
    const config = hook.tasks[0].config as Record<string, unknown>;
    expect((config.config as Record<string, unknown>).id).toBe("pmm-sol");
    expect(config.backtesting_resolution).toBe("1m");
  });

  it("ignores instances of every other routine", async () => {
    getRoutineInstances.mockResolvedValue([
      instance({ instance_id: "other", routine_name: "portfolio_snapshot" }),
    ]);

    await render();

    expect(hook.tasks).toHaveLength(0);
  });

  it("surfaces a failed run's own message rather than a generic one", async () => {
    getRoutineInstances.mockResolvedValue([
      instance({
        status: "failed",
        error: "Backtest timed out. Render it later with task_id=abc123.",
      }),
    ]);
    getRoutineInstance.mockResolvedValue(
      instance({
        status: "failed",
        error: "Backtest timed out. Render it later with task_id=abc123.",
      }),
    );

    await render();
    await flush();

    expect(hook.selectedTask?.status).toBe("failed");
    expect(hook.selectedTask?.error).toContain("task_id=abc123");
  });
});

describe("handover to the archive", () => {
  it("reads the task id out of the metrics row by key", () => {
    expect(
      taskIdFromInstance(
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        instance({
          status: "completed",
          table_data: [{ net_pnl_quote: 12.5, task_id: "task-9" }],
          table_columns: ["net_pnl_quote", "task_id"],
        }) as any,
      ),
    ).toBe("task-9");

    expect(taskIdFromInstance(null)).toBeNull();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(taskIdFromInstance(instance({ table_data: [] }) as any)).toBeNull();
  });

  it("switches the selection to the stored run once it completes", async () => {
    getRoutineInstances.mockResolvedValue([instance()]);
    getRoutineInstance.mockResolvedValue(
      instance({
        status: "completed",
        table_data: [{ task_id: "task-9", net_pnl_quote: 12.5 }],
      }),
    );
    getArchivedBacktest.mockResolvedValue({
      task_id: "task-9",
      status: "completed",
      result: {},
    });

    await render();
    await flush();

    expect(hook.selectedTaskId).toBe("task-9");
    expect(getArchivedBacktest).toHaveBeenCalledWith("task-9");
  });
});

describe("removing a run", () => {
  it("stops an in-flight run instead of deleting nothing", async () => {
    getRoutineInstances.mockResolvedValue([instance()]);
    getRoutineInstance.mockResolvedValue(instance());

    await render();

    await act(async () => {
      hook.remove.mutate("inst-1");
    });
    await flush();

    expect(stopRoutineInstance).toHaveBeenCalledWith("inst-1");
    expect(deleteArchivedBacktest).not.toHaveBeenCalled();
  });

  it("deletes a stored run through the archive, whichever server ran it", async () => {
    listBacktestArchive.mockResolvedValue({
      migrated: true,
      summaries: [
        { task_id: "task-9", status: "completed", server: "local", saved: true },
      ],
    });

    await render();
    await flush();

    await act(async () => {
      hook.remove.mutate("task-9");
    });
    await flush();

    expect(deleteArchivedBacktest).toHaveBeenCalledWith("task-9");
    expect(stopRoutineInstance).not.toHaveBeenCalled();
  });
});
