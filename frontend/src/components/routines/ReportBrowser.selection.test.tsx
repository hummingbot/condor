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
import type { ReportSummary, RoutineInfo } from "@/lib/api";

const getRoutineReports = vi.fn();
const deleteReport = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getRoutines: () => Promise.resolve([routine("alpha_check")]),
    getRoutineReports: (...a: unknown[]) => getRoutineReports(...a),
    getRoutineInstance: () => Promise.resolve(null),
    getRoutineSource: () => Promise.resolve({ filename: "x.py", source: "" }),
    runRoutine: () => Promise.resolve({ instance_id: "i9" }),
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

async function render() {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider value={{ server: "dashboard-server", setServer: () => {} }}>
          <ReportBrowser hosted instances={[]} onClose={() => {}} />
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
