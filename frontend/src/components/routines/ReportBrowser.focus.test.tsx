/**
 * The library, opened on one thing (FEAT-077).
 *
 * A dock row does not mean "the routine library" — it means *this run*, of
 * *that* routine. These cases pin what the pane owes such a row: the routine it
 * names however it happens to spell it, the report it pointed at rather than
 * the newest, and the run's own text when it wrote no report at all. Plus the
 * control that makes the pane navigable at all beside a chat: the scope picker,
 * which lives in the header because the routine list is a 48px rail there.
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
const getRoutineInstance = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getRoutines: () =>
      Promise.resolve([
        routine("alpha_check"),
        routine("scout/beta_check", "agent:scout"),
      ]),
    getRoutineReports: (...a: unknown[]) => getRoutineReports(...a),
    getRoutineInstance: (...a: unknown[]) => getRoutineInstance(...a),
    getRoutineSource: () => Promise.resolve({ filename: "x.py", source: "" }),
    runRoutine: () => Promise.resolve({ instance_id: "i9" }),
    scheduleRoutine: () => Promise.resolve({ instance_id: "i9" }),
    deleteReport: () => Promise.resolve({ deleted: true }),
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

function routine(name: string, source = "routine"): RoutineInfo {
  return {
    name,
    description: name,
    is_continuous: false,
    category: "general",
    source,
    fields: {},
    last_modified: null,
    report_count: 0,
  } as RoutineInfo;
}

function report(id: string, created_at: string): ReportSummary {
  return {
    id,
    title: id,
    source_name: "scout/beta_check",
    source_type: "routine",
    filename: `${id}.html`,
    created_at,
  } as unknown as ReportSummary;
}

/** A run that answered in text: no report, so its output is all there is. */
const TEXT_RUN = {
  instance_id: "i1",
  routine_name: "alpha_check",
  config: {},
  status: "completed",
  source: "web",
  run_count: 1,
  last_result: "Nothing to arbitrage today",
  last_run_at: "2026-08-28T10:00:00Z",
  created_at: "2026-08-28T10:00:00Z",
} as unknown as RoutineInstance;

let container: HTMLDivElement;
let root: Root;

async function render(props: Record<string, unknown> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider
          value={{ server: "dashboard-server", setServer: () => {} }}
        >
          <ReportBrowser hosted instances={[]} onClose={() => {}} {...props} />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const heading = () => container.querySelector("h2")?.textContent?.trim();
const scope = () =>
  container.querySelector<HTMLSelectElement>(
    'select[aria-label="Routine scope"]',
  )!;
const frame = () => container.querySelector('[data-testid="frame"]')?.textContent;
const tab = (name: string) =>
  [...container.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === name,
  )!;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  getRoutineReports.mockResolvedValue({ reports: [] });
  getRoutineInstance.mockResolvedValue(TEXT_RUN);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("the library's scope picker", () => {
  it("sits in the header, where the collapsed rail cannot hide it", async () => {
    await render();

    // Hosted, the routine list is a 48px rail — the filter it used to live in
    // is unreachable there, which made it no filter at all.
    expect(container.querySelector(".w-12")).toBeTruthy();
    expect(scope()).toBeTruthy();
    expect(scope().value).toBe("all");
  });

  it("narrows the list, and takes the reader with it", async () => {
    await render();
    expect(heading()).toBe("Alpha Check");

    await act(async () => {
      scope().value = "scout";
      scope().dispatchEvent(new Event("change", { bubbles: true }));
    });

    // Narrowing to an agent whose routines exclude the open one has to move to
    // one that is in scope, or the pane shows what its own list denies.
    expect(scope().value).toBe("scout");
    expect(heading()).toBe("Beta Check");
  });
});

describe("the library opened on one run", () => {
  it("opens on the report the row pointed at, not the newest", async () => {
    getRoutineReports.mockResolvedValue({
      reports: [
        report("newest", "2026-08-28T12:00:00Z"),
        report("clicked", "2026-08-28T09:00:00Z"),
      ],
    });

    await render({
      initialSource: "scout/beta_check",
      initialReportId: "clicked",
    });

    expect(frame()).toBe("clicked");
  });

  it("adopts the library's spelling of the routine a report names", async () => {
    getRoutineReports.mockResolvedValue({ reports: [] });

    // The report index files an agent's routine under its bare name; the
    // library calls it `{slug}/{name}`, and Run and Config only work on that.
    await render({ initialSource: "beta_check" });

    // The header titles the routine, not its key (READ-276) — what proves the
    // pane resolved the spelling is the key it goes on to fetch under.
    expect(heading()).toBe("Beta Check");
    expect(getRoutineReports).toHaveBeenCalledWith("scout/beta_check");
  });

  it("opens a run that wrote no report on its own output", async () => {
    await render({
      initialSource: "alpha_check",
      initialInstanceId: "i1",
      instances: [TEXT_RUN],
    });

    expect(container.textContent).toContain("Nothing to arbitrage today");

    // And the reports are still one click away, not a different pane.
    await act(async () => {
      tab("report").click();
    });
    expect(container.textContent).toContain("No reports yet");
  });
});
