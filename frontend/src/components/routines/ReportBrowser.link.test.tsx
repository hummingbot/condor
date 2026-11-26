/**
 * The /routines page follows a `?routine=` link it is already showing.
 *
 * The route keeps its ReportBrowser across a param-only navigation, so the
 * routine a link names arrives as a changed `initialSource` on a mounted
 * component. It used to be read once, at mount: a second link left the page
 * on whatever it had open, and on a phone, where the page shows the list or
 * the report but never both, the list stayed up as if nothing had happened.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import type { RoutineInfo } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    getRoutines: () =>
      Promise.resolve([routine("alpha_check"), routine("beta_check")]),
    getRoutineReports: () => Promise.resolve({ reports: [] }),
    getRoutineInstance: () => Promise.resolve(null),
    getRoutineSource: () => Promise.resolve({ filename: "x.py", source: "" }),
    runRoutine: () => Promise.resolve({ instance_id: "i9" }),
    scheduleRoutine: () => Promise.resolve({ instance_id: "i9" }),
    deleteReport: () => Promise.resolve({ deleted: true }),
    getReportHtml: () => Promise.resolve(""),
    getRoutineHooks: () => Promise.resolve({}),
  },
}));

vi.mock("./ReportFrame", () => ({
  ReportFrame: ({ reportId }: { reportId: string }) => <div>{reportId}</div>,
}));

/** Whether the window reads as phone-width to `useIsPhone`. */
let phone = false;
window.matchMedia = ((media: string) => ({
  matches: phone && media.includes("max-width"),
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

let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;

/** The page as `/routines` mounts it, with `?routine=` as `initialSource`. */
async function show(initialSource?: string) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider value={{ server: "s", setServer: () => {} }}>
          <ReportBrowser page instances={[]} initialSource={initialSource} />
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

const heading = () => container.querySelector("h2")?.textContent?.toLowerCase() ?? "";
/** The report column; on a phone it carries `hidden` while the list has the screen. */
const reportColumn = () =>
  [...container.querySelectorAll("div")].find((d) =>
    d.className.startsWith("min-w-0 flex-1 flex-col"),
  )!;
const reportHiddenOnPhone = () => reportColumn().className.split(" ").includes("hidden");

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  phone = false;
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("a ?routine= link on a mounted /routines page", () => {
  it("opens the routine it names", async () => {
    await show("alpha_check");
    expect(heading()).toContain("alpha");
    await show("beta_check");
    expect(heading()).toContain("beta");
  });

  it("turns a phone from the list to that routine's report", async () => {
    phone = true;
    await show();
    expect(reportHiddenOnPhone()).toBe(true);
    await show("beta_check");
    expect(reportHiddenOnPhone()).toBe(false);
    expect(heading()).toContain("beta");
  });

  it("leaves the reader's own pick alone while the link is unchanged", async () => {
    await show("alpha_check");
    const beta = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.toLowerCase().includes("beta"),
    )!;
    await act(async () => beta.click());
    expect(heading()).toContain("beta");
    // The same link again, as any re-render of the route passes it.
    await show("alpha_check");
    expect(heading()).toContain("beta");
  });
});
