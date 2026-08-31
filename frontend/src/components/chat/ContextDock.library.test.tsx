/**
 * The routine library, opened beside the conversation (FEAT-077).
 *
 * The dock's Routines section shows the recent end of what this conversation
 * ran; the library is all of it. What is pinned here is the wiring that makes
 * that one pane the only way a routine is read: the header's "Browse all" opens
 * it scoped to the agent being talked to, a row opens the *same* pane focused
 * on the run it points at, and it is handed the conversation so a run from it
 * belongs here.
 *
 * And that the door cannot go missing. The dock collapses itself on a narrow
 * window and stays collapsed once closed, so the rail carries the library too,
 * and a pane already open outlives the column it was opened from.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RoutineInstance } from "@/lib/api";
import { WorkspacePaneOutlet, WorkspacePaneProvider } from "./WorkspacePane";

const getRoutineInstances = vi.fn();
const getReports = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getRoutineInstances: (...a: unknown[]) => getRoutineInstances(...a),
    getReports: (...a: unknown[]) => getReports(...a),
    getRoutineInstance: (id: string) =>
      Promise.resolve({ instance_id: id, routine_name: "alpha", status: "completed" }),
    stopDelegation: () => Promise.resolve({}),
  },
}));

/** The library itself is tested in ReportBrowser.hosted.test.tsx. */
let browserProps: Record<string, unknown> = {};
vi.mock("@/components/routines/ReportBrowser", () => ({
  ReportBrowser: (props: Record<string, unknown>) => {
    browserProps = props;
    return <div data-testid="library" />;
  },
}));

/** jsdom answers every query `false`; the dock and the pane turn on width. */
window.matchMedia = ((media: string) => ({
  matches: true,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

const { ContextDock } = await import("./ContextDock");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const CONVERSATION = "conv-1";
const RUN_CONTEXT = {
  serverName: "chat-server",
  sessionKey: "web:42:main",
  agentSlug: "scout",
};

const INSTANCE = {
  instance_id: "i1",
  routine_name: "scout/alpha_check",
  config: {},
  status: "completed",
  source: "web",
  conversation_id: CONVERSATION,
  run_count: 1,
  last_run_at: "2026-08-28T10:00:00Z",
  created_at: "2026-08-28T10:00:00Z",
} as unknown as RoutineInstance;

let container: HTMLDivElement;
let root: Root;

async function render() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <WorkspacePaneProvider>
            <WorkspacePaneOutlet />
            <ContextDock
              delegations={[]}
              conversationId={CONVERSATION}
              agentSlug="scout"
              agentName="Scout"
              runContext={RUN_CONTEXT}
            />
          </WorkspacePaneProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const pane = () =>
  container.querySelector<HTMLElement>('aside[aria-label="Workspace pane"]')!;
const library = () => document.querySelector('[data-testid="library"]');
const runRow = () =>
  [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Alpha Check"),
  )!;
const reportRow = () =>
  [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Beta Check"),
  )!;
const browseAll = () =>
  [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Browse all"),
  )!;
const byTitle = (title: string) =>
  container.querySelector<HTMLButtonElement>(`button[title="${title}"]`)!;

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  browserProps = {};
  getRoutineInstances.mockResolvedValue([INSTANCE]);
  getReports.mockResolvedValue({ reports: [] });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("the dock's routine library", () => {
  it("opens in the pane, scoped to the agent and to this conversation", async () => {
    await render();
    expect(library()).toBeNull();

    await click(browseAll());

    expect(pane().contains(library())).toBe(true);
    expect(browserProps.hosted).toBe(true);
    expect(browserProps.initialSourceTypeFilter).toBe("scout");
    expect(browserProps.runContext).toEqual(RUN_CONTEXT);
    // "Browse all" is the whole library: nothing is picked out of it.
    expect(browserProps.initialSource).toBeUndefined();
  });

  it("is what a row opens, focused on the run it points at", async () => {
    await render();

    await click(runRow());

    expect(pane().contains(library())).toBe(true);
    expect(browserProps.initialSource).toBe("scout/alpha_check");
    expect(browserProps.initialInstanceId).toBe("i1");
    // This run wrote no report, so there is none to open it on — its output is
    // the whole of what it produced.
    expect(browserProps.initialReportId).toBeUndefined();
  });

  it("opens a report row on that report, not on the newest", async () => {
    getReports.mockResolvedValue({
      reports: [
        {
          id: "rep-7",
          title: "Beta Check",
          source_name: "beta_check",
          source_type: "routine",
          filename: "beta.html",
          created_at: "2026-08-28T09:00:00Z",
        },
      ],
    });
    await render();

    await click(reportRow());

    expect(browserProps.initialSource).toBe("beta_check");
    expect(browserProps.initialReportId).toBe("rep-7");
  });

  it("keeps a door to it while the dock is collapsed", async () => {
    await render();

    await click(byTitle("Collapse"));
    expect(browseAll()).toBeUndefined();

    // The rail's own, so a reload that wakes up collapsed still has one.
    await click(byTitle("Browse all routines — config, schedule and reports"));
    expect(pane().contains(library())).toBe(true);
  });

  it("outlives the column it was opened from", async () => {
    await render();

    await click(browseAll());
    await click(byTitle("Collapse"));

    // Tidying the dock away is not closing what you were reading.
    expect(pane().contains(library())).toBe(true);
  });
});
