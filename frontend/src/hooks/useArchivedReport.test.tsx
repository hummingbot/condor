/**
 * FEAT-079: an archived run's chart is looked up before it is generated.
 *
 * An archived run is immutable, so charting one is "generate once, look it up
 * forever". What that buys is pinned here: a stored report is rendered without
 * launching anything, a miss offers the run and launches the routine exactly
 * once, and a run and one of its controllers are separate subjects — asking for
 * one never answers with the other's chart.
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

type StoredReport = {
  report_id: string | null;
  created_at: string | null;
  title: string;
};

const getArchivedReport = vi.fn<(...a: Args) => Promise<StoredReport>>();
const getRoutineInstance = vi.fn<(...a: Args) => Promise<unknown>>();
const runRoutine = vi.fn<(...a: Args) => Promise<{ instance_id: string }>>();

vi.mock("@/lib/api", () => ({
  api: {
    getArchivedReport: (...a: Args) => getArchivedReport(...a),
    getRoutineInstance: (...a: Args) => getRoutineInstance(...a),
    runRoutine: (...a: Args) => runRoutine(...a),
  },
}));

const { useArchivedReport, ARCHIVED_ROUTINE } = await import("./useArchivedReport");

type Hook = ReturnType<typeof useArchivedReport>;

const SERVER = "brigado_2";
const DB = "/data/bots/archive/run.sqlite";

let container: HTMLDivElement;
let root: Root;
let hook: Hook;

function Probe({ controllerId }: { controllerId: string }) {
  const value = useArchivedReport(SERVER, DB, controllerId);
  // Published from an effect, never during render: `act` flushes effects, so
  // the probe is current by the time a test reads it.
  useEffect(() => {
    hook = value;
  });
  return null;
}

const flush = () =>
  act(async () => void (await new Promise((r) => setTimeout(r, 0))));

async function render(controllerId = "") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Probe controllerId={controllerId} />
      </QueryClientProvider>,
    );
  });
  await flush();
}

function instance(over: Record<string, unknown> = {}) {
  return {
    instance_id: "inst-1",
    routine_name: ARCHIVED_ROUTINE,
    status: "completed",
    source: "web",
    server_name: SERVER,
    config: {},
    created_at: 1_756_000_000,
    last_run_at: null,
    last_result: null,
    last_duration: null,
    run_count: 1,
    report_id: "rep-new",
    error: null,
    ...over,
  };
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
    true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.clearAllMocks();
  getRoutineInstance.mockResolvedValue(null);
  runRoutine.mockResolvedValue({ instance_id: "inst-1" });
  getArchivedReport.mockResolvedValue({
    report_id: null,
    created_at: null,
    title: "",
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("looking a chart up", () => {
  it("renders the stored report without launching anything", async () => {
    getArchivedReport.mockResolvedValue({
      report_id: "rep-1",
      created_at: "2026-08-31T00:00:00Z",
      title: "Archived Bot — run",
    });

    await render();

    expect(hook.reportId).toBe("rep-1");
    expect(runRoutine).not.toHaveBeenCalled();
  });

  it("asks for the run and the controller as different subjects", async () => {
    await render("pmm_1");

    expect(getArchivedReport).toHaveBeenCalledWith(SERVER, DB, "pmm_1");
  });

  it("treats a miss as an offer to chart, not an error", async () => {
    await render();

    expect(hook.reportId).toBeNull();
    expect(hook.isRunning).toBe(false);
    expect(hook.error).toBeNull();
  });
});

describe("charting", () => {
  it("runs the archived_analyzer routine once, for this controller, quietly", async () => {
    await render("pmm_1");

    await act(async () => {
      hook.chart();
    });
    await flush();

    expect(runRoutine).toHaveBeenCalledTimes(1);
    expect(runRoutine).toHaveBeenCalledWith(SERVER, ARCHIVED_ROUTINE, {
      mode: "detail",
      db_path: DB,
      controller_id: "pmm_1",
      // The dashboard embeds the report; a PNG nobody will look at is waste.
      chart: false,
    });
  });

  it("follows the instance it launched and takes the report it rendered", async () => {
    getRoutineInstance.mockResolvedValue(instance({ status: "running" }));

    await render();
    await act(async () => {
      hook.chart();
    });
    await flush();

    expect(hook.isRunning).toBe(true);

    // The run lands, and the lookup is re-asked: that the subject was stamped
    // is proven by the store, not assumed from the instance.
    getRoutineInstance.mockResolvedValue(instance({ status: "completed" }));
    getArchivedReport.mockResolvedValue({
      report_id: "rep-new",
      created_at: "2026-08-31T00:00:01Z",
      title: "Archived Bot — run",
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 2100));
    });
    await flush();

    expect(hook.reportId).toBe("rep-new");
    expect(hook.isRunning).toBe(false);
  });

  it("surfaces a failed run's own error instead of a blank panel", async () => {
    getRoutineInstance.mockResolvedValue(
      instance({ status: "failed", report_id: null, error: "no such database" }),
    );

    await render();
    await act(async () => {
      hook.chart();
    });
    await flush();

    expect(hook.error).toBe("no such database");
    expect(hook.reportId).toBeNull();
  });
});
