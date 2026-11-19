/**
 * The open report is a report, not a position in a list (FP-07).
 *
 * A scheduled routine's report list is refetched every 10 s and grows at the
 * front. The browser held its pick as an array index, so a refresh that
 * prepended a report slid a *different* report under the reader — and Delete,
 * armed as a bare flag and reading the id at confirm time, then removed that
 * neighbour. These cases pin the pick and the armed delete to the report's id.
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
import type { ReportSummary, RoutineInfo, RoutineInstance } from "@/lib/api";

const getRoutineReports = vi.fn();
const deleteReport = vi.fn();
const runRoutine = vi.fn();
const getRoutineInstance = vi.fn();
const stopRoutineInstance = vi.fn();
const getRoutines = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getRoutines: (...a: unknown[]) => getRoutines(...a),
    getRoutineReports: (...a: unknown[]) => getRoutineReports(...a),
    getRoutineInstance: (...a: unknown[]) => getRoutineInstance(...a),
    stopRoutineInstance: (...a: unknown[]) => stopRoutineInstance(...a),
    getRoutineSource: () => Promise.resolve({ filename: "x.py", source: "" }),
    getRoutineFields: () => Promise.resolve({ fields: {} }),
    runRoutine: (...a: unknown[]) => runRoutine(...a),
    scheduleRoutine: () => Promise.resolve({ instance_id: "i9" }),
    deleteReport: (...a: unknown[]) => deleteReport(...a),
    getReportHtml: () => Promise.resolve(""),
    getRoutineHooks: () => Promise.resolve({}),
  },
}));

/** The report body is an authenticated iframe; only which one is open matters. */
vi.mock("./ReportFrame", () => ({
  ReportFrame: ({ reportId }: { reportId: string }) => (
    <div data-testid="frame">{reportId}</div>
  ),
}));

window.matchMedia = ((media: string) => ({
  matches: false,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;
Element.prototype.scrollIntoView = () => {};

const { ReportBrowser } = await import("./ReportBrowser");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function routine(name: string): RoutineInfo {
  return {
    name,
    description: name,
    is_continuous: false,
    category: "general",
    source: "routine",
    fields: {},
    last_modified: null,
    report_count: 0,
  } as RoutineInfo;
}

function report(id: string): ReportSummary {
  return {
    id,
    title: id,
    source_name: "alpha_check",
    source_type: "routine",
    filename: `${id}.html`,
    created_at: "2026-08-28T10:00:00Z",
  } as unknown as ReportSummary;
}

let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;

const settle = async (turns = 4) => {
  for (let i = 0; i < turns; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
};

async function render(instances: RoutineInstance[] = []) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider value={{ server: "dashboard-server", setServer: () => {} }}>
          <ReportBrowser hosted instances={instances} onClose={() => {}} />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  await settle();
}

/** What the scheduled routine's 10 s poll does: the list again, newest first. */
async function poll(ids: string[]) {
  getRoutineReports.mockResolvedValue({ reports: ids.map(report) });
  await act(async () => {
    await qc.invalidateQueries({ queryKey: ["routine-reports"] });
  });
  await settle(2);
}

const frame = () => container.querySelector('[data-testid="frame"]')?.textContent;
const titled = (title: string) =>
  container.querySelector<HTMLButtonElement>(`button[title="${title}"]`);
const click = (el: HTMLElement | null) => {
  if (!el) throw new Error("nothing to click");
  return act(async () => {
    el.click();
  });
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  getRoutineReports.mockReset();
  getRoutineReports.mockResolvedValue({ reports: [report("b"), report("a")] });
  deleteReport.mockReset();
  deleteReport.mockResolvedValue({ deleted: true });
  runRoutine.mockReset();
  runRoutine.mockResolvedValue({ instance_id: "i9" });
  getRoutineInstance.mockReset();
  getRoutineInstance.mockResolvedValue(null);
  stopRoutineInstance.mockReset();
  stopRoutineInstance.mockResolvedValue({ stopped: true });
  getRoutines.mockReset();
  getRoutines.mockResolvedValue([routine("alpha_check")]);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("ReportBrowser selection", () => {
  it("keeps the picked report open when a newer one is prepended", async () => {
    await render();
    expect(frame()).toBe("b");

    // The timeline strip names each report by its title.
    await click(titled("a"));
    expect(frame()).toBe("a");

    await poll(["c", "b", "a"]);

    expect(frame()).toBe("a");
    expect(container.textContent).toContain("3 of 3");
  });

  it("leaves a reader who picked nothing on the newest", async () => {
    await render();
    await poll(["c", "b", "a"]);
    expect(frame()).toBe("c");
  });

  it("deletes the report Delete was armed for, whatever the list did since", async () => {
    await render();
    await click(titled("a"));
    await click(titled("Delete report"));

    await poll(["c", "b", "a"]);
    await click(titled("Confirm delete report"));

    expect(deleteReport).toHaveBeenCalledTimes(1);
    expect(deleteReport.mock.calls[0][0]).toBe("a");
  });

  it("disarms Delete when the reader moves to another report", async () => {
    await render();
    await click(titled("a"));
    await click(titled("Delete report"));
    expect(titled("Confirm delete report")).not.toBeNull();

    await click(titled("b"));

    expect(frame()).toBe("b");
    expect(titled("Confirm delete report")).toBeNull();
    expect(deleteReport).not.toHaveBeenCalled();
  });
});

/**
 * A run that fails is said where it can be seen (FU-08).
 *
 * Every case here has reports on file: that is the path where the previous
 * report stayed up and read as the result, with the error behind a collapsed
 * panel or an Output toggle the view never switched to.
 */
describe("ReportBrowser run status, with reports on file", () => {
  const strip = () => container.querySelector("[data-status-strip]")?.textContent ?? null;

  it("shows a rejected run POST in the status strip", async () => {
    runRoutine.mockRejectedValue(new Error("server unreachable"));
    await render();
    expect(frame()).toBe("b");
    expect(strip()).toBeNull();

    await click(titled("Run with current config"));
    await settle();

    expect(strip()).toContain("Could not run alpha check: server unreachable");
  });

  it("surfaces a run that ended failed, and opens its output", async () => {
    getRoutineInstance.mockResolvedValue({
      instance_id: "i9",
      routine_name: "alpha_check",
      config: {},
      status: "failed",
      source: "web",
      run_count: 1,
      created_at: 1,
      last_run_at: 1,
      last_result: null,
      last_duration: null,
      error: "KeyError: 'price'\n  File x.py, line 3",
    });
    await render();

    await click(titled("Run with current config"));
    await settle();

    expect(strip()).toContain("Last run failed: KeyError: 'price'");
    // The Output view is open on that run — not the previous report.
    expect(frame()).toBeUndefined();
    expect(container.querySelector("pre")?.textContent).toContain("File x.py, line 3");
  });

  it("asks before stopping from the running chip, and shows a failed stop", async () => {
    stopRoutineInstance.mockRejectedValue(new Error("no such instance"));
    await render([
      {
        instance_id: "i1",
        routine_name: "alpha_check",
        config: {},
        status: "running",
        source: "web",
        run_count: 3,
        created_at: 1,
        last_run_at: 1,
        last_result: null,
        last_duration: null,
      },
    ]);

    await click(titled("Stop"));
    expect(stopRoutineInstance).not.toHaveBeenCalled();

    await click(titled("Confirm stop"));
    await settle();

    expect(stopRoutineInstance).toHaveBeenCalledWith("i1");
    expect(strip()).toContain("Could not stop the run: no such instance");
  });
});

/**
 * A run's outcome belongs to the routine that started it.
 *
 * The pane polls the run it started; the reader can move to another routine
 * meanwhile. Tied to whichever routine was on screen when the run ended, a
 * failure of alpha opened beta's Output view and captioned alpha's error
 * under beta's name.
 */
describe("ReportBrowser run status, across a routine switch", () => {
  const strip = () => container.querySelector("[data-status-strip]")?.textContent ?? null;
  const instance = (status: RoutineInstance["status"], error?: string): RoutineInstance =>
    ({
      instance_id: "i9",
      routine_name: "alpha_check",
      config: {},
      status,
      source: "web",
      run_count: 1,
      created_at: 1,
      last_run_at: 1,
      last_result: null,
      last_duration: null,
      error,
    }) as RoutineInstance;
  /** What the 2 s completion poll does, without waiting two seconds. */
  const pollRun = async () => {
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ["routine-instance"] });
    });
    await settle();
  };

  beforeEach(() => {
    getRoutines.mockResolvedValue([routine("alpha_check"), routine("beta_check")]);
  });

  it("keeps a failure of the routine left behind off the one on screen, and says it on return", async () => {
    getRoutineInstance.mockResolvedValue(instance("running"));
    await render();
    expect(container.querySelector("h2")?.textContent).toBe("Alpha Check");

    await click(titled("Run with current config"));
    await settle();
    expect(runRoutine.mock.calls[0][1]).toBe("alpha_check");

    await click(titled("beta check"));
    await settle();
    expect(container.querySelector("h2")?.textContent).toBe("Beta Check");
    // alpha's run in flight is not beta's either.
    expect(strip()).toBeNull();

    getRoutineInstance.mockResolvedValue(instance("failed", "KeyError: 'price'"));
    await pollRun();

    // beta is untouched: no strip, and still on its report rather than an
    // Output view of alpha's run.
    expect(strip()).toBeNull();
    expect(frame()).toBe("b");
    expect(container.textContent).not.toContain("KeyError");

    await click(titled("alpha check"));
    await settle();
    expect(strip()).toContain("Last run failed: KeyError: 'price'");
  });

  it("still opens the output when the routine that failed is the one on screen", async () => {
    getRoutineInstance.mockResolvedValue(instance("running"));
    await render();
    await click(titled("Run with current config"));
    await settle();

    getRoutineInstance.mockResolvedValue(instance("failed", "KeyError: 'price'\n  File x.py, line 3"));
    await pollRun();

    expect(strip()).toContain("Last run failed: KeyError: 'price'");
    expect(frame()).toBeUndefined();
  });
});
