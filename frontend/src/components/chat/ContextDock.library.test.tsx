/**
 * The routine library, opened beside the conversation (FEAT-077).
 *
 * The dock's Routines section shows the recent end of what this conversation
 * ran; the library is all of it. What is pinned here is the wiring that makes
 * that one pane the only way a routine is read: a row opens it focused on the
 * run it points at, scoped to the agent being talked to, and it is handed the
 * conversation so a run from it belongs here. The dock has no door onto the
 * unfocused library — the runs are the doors, and /routines is the whole of it.
 *
 * And that a pane already open outlives the column it was opened from: the
 * dock stays exactly as the reader left it when the library comes up, and
 * collapsing it by hand afterwards does not take the library down with it.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RoutineInfo, RoutineInstance } from "@/lib/api";
import type { LibraryFocus } from "./DockRoutines";
import { WorkspacePaneOutlet, WorkspacePaneProvider } from "./WorkspacePane";

const getRoutineInstances = vi.fn();
const getReports = vi.fn();
const getRoutines = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getRoutineInstances: (...a: unknown[]) => getRoutineInstances(...a),
    getReports: (...a: unknown[]) => getReports(...a),
    getRoutines: (...a: unknown[]) => getRoutines(...a),
    getRoutineInstance: (id: string) =>
      Promise.resolve({
        instance_id: id,
        routine_name: "alpha",
        status: "completed",
      }),
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

function routine(name: string, owner?: string): RoutineInfo {
  return {
    name,
    description: `${name} description`,
    is_continuous: false,
    category: "general",
    source: owner ? `agent:${owner}` : "routine",
    fields: {},
    last_modified: null,
    report_count: 0,
  };
}

/** Two of the agent's own, one general, one another agent's — enough for the
 * scope to mean something and to be able to change. */
const LIBRARY = [
  routine("scout/alpha_check", "scout"),
  routine("scout/depth_watch", "scout"),
  routine("sage/funding_watch", "sage"),
  routine("mcap_comparison"),
];

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

let qc: QueryClient;

/**
 * The workspace around the dock, reduced to what it interacts with.
 *
 * The pane's occupant is the workspace's state, not the dock's (FEAT-081), so
 * the harness holds it exactly as `AgentChatTab` does.
 */
function Workspace({ agentSlug }: { agentSlug: string }) {
  const [library, setLibrary] = useState<LibraryFocus | null>(null);
  return (
    <WorkspacePaneProvider>
      <WorkspacePaneOutlet />
      <ContextDock
        delegations={[]}
        conversationId={CONVERSATION}
        agentSlug={agentSlug}
        agentName="Scout"
        runContext={RUN_CONTEXT}
        library={library}
        onLibraryChange={setLibrary}
      />
    </WorkspacePaneProvider>
  );
}

/** @param agentSlug who the conversation on screen is bound to. */
async function render(agentSlug = "scout") {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <Workspace agentSlug={agentSlug} />
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
const byTitle = (title: string) =>
  container.querySelector<HTMLButtonElement>(`button[title="${title}"]`)!;
/** Which routine the library is on — the sheet's own nav bar carries this. */
const trigger = () =>
  container.querySelector<HTMLButtonElement>('button[aria-label="Routine"]')!;
/** Whose routines it lists — asked in the library's bar, beside which one. */
const scopeSelect = () =>
  container.querySelector<HTMLSelectElement>(
    'select[aria-label="Routine scope"]',
  )!;
/** The picker's list is portalled out of the dock, so it is read off the body. */
const options = () => [
  ...document.querySelectorAll<HTMLButtonElement>("[data-routine-row]"),
];
const option = (label: string) =>
  options().find((b) => b.textContent?.includes(label))!;

async function press(key: string, on: EventTarget) {
  await act(async () => {
    on.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
}

async function setScope(value: string) {
  await act(async () => {
    scopeSelect().value = value;
    scopeSelect().dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  localStorage.clear();
  browserProps = {};
  getRoutineInstances.mockResolvedValue([INSTANCE]);
  getReports.mockResolvedValue({ reports: [] });
  getRoutines.mockResolvedValue(LIBRARY);
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
  it("opens in the pane, focused on the run the row points at", async () => {
    await render();
    expect(library()).toBeNull();

    await click(runRow());

    expect(pane().contains(library())).toBe(true);
    expect(browserProps.hosted).toBe(true);
    expect(browserProps.initialSourceTypeFilter).toBe("scout");
    expect(browserProps.runContext).toEqual(RUN_CONTEXT);
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

  it("leaves the column where the reader put it", async () => {
    await render();
    expect(byTitle("Collapse")).toBeTruthy();

    await click(runRow());

    // The rows are the doors into the library, so folding them away at the
    // moment it opens means putting the column back to read a second routine.
    // The column is the reader's; nothing else touches it.
    expect(pane().contains(library())).toBe(true);
    expect(byTitle("Collapse")).toBeTruthy();

    await click(byTitle("Close"));

    expect(library()).toBeNull();
    expect(byTitle("Collapse")).toBeTruthy();
  });

  it("outlives the column it was opened from", async () => {
    await render();

    await click(runRow());
    await click(byTitle("Collapse"));

    // Collapsing the column is not a close: the sheet is a sibling of the
    // `aside`, not a child, so what the reader was reading stays mounted in
    // the pane rather than being torn down and rebuilt.
    expect(pane().contains(library())).toBe(true);
    expect(browserProps.hosted).toBe(true);
  });
});

/**
 * The pair of controls that say what the library is reading, split across the
 * two surfaces that own the questions: the dock answers "whose routines", next
 * to the runs this conversation has made, and the library's own nav bar answers
 * "which one", over the report it names.
 */
describe("the dock's routine picker", () => {
  it("opens the library on the routine picked from its nav bar", async () => {
    await render();
    // Which routine is a question about a library that is open.
    expect(trigger()).toBeNull();

    await click(runRow());
    await click(trigger());
    // Scoped to the agent being talked to: its own two, not the other two.
    expect(options()).toHaveLength(2);
    expect(option("Depth Watch")).toBeTruthy();

    await click(option("Depth Watch"));

    expect(pane().contains(library())).toBe(true);
    expect(browserProps.initialSource).toBe("scout/depth_watch");
    // The pane no longer lists or titles the routine — this picker does.
    expect(browserProps.externalPicker).toBe(true);
  });

  it("steps through the list with the arrows, as the sidebar did", async () => {
    await render();

    await click(runRow());
    await click(trigger());
    await click(option("Alpha Check"));
    expect(browserProps.initialSource).toBe("scout/alpha_check");

    await press("ArrowDown", trigger());
    expect(browserProps.initialSource).toBe("scout/depth_watch");

    await press("ArrowUp", trigger());
    expect(browserProps.initialSource).toBe("scout/alpha_check");
  });

  it("narrows to Condor's own library, and takes the reader with it", async () => {
    await render();

    await click(runRow());
    await click(trigger());
    await click(option("Alpha Check"));

    await setScope("condor");

    // The open routine is not in the new scope, so the library moves to one
    // that is rather than showing what its own list denies.
    expect(browserProps.initialSource).toBe("mcap_comparison");
    expect(browserProps.initialSourceTypeFilter).toBe("condor");
  });

  it("widens itself for a run that points outside the scope", async () => {
    getRoutineInstances.mockResolvedValue([
      { ...INSTANCE, routine_name: "mcap_comparison" },
    ]);
    await render();

    await click(
      [...container.querySelectorAll("button")].find((b) =>
        b.textContent?.includes("Mcap Comparison"),
      )!,
    );

    // A shared routine this conversation ran is not one of the agent's own:
    // the picker widens rather than hiding what was just clicked.
    expect(scopeSelect().value).toBe("all");
    expect(browserProps.initialSource).toBe("mcap_comparison");
  });
});

/**
 * Whose routines, by default: the agent you are talking to.
 *
 * Opening the library from an agent means that agent's routines. A pick of the
 * reader's own stands for as long as they stay in the conversation — "All
 * routines" keeps meaning all of them — but the next agent re-asks the
 * question, rather than leaving the last one's filter over a list it does not
 * own.
 */
describe("the scope's default", () => {
  it("is the agent being talked to", async () => {
    await render();
    await click(runRow());

    expect(scopeSelect().value).toBe("scout");
  });

  it("is every routine in the unbound Condor conversation", async () => {
    await render("");
    await click(runRow());

    expect(scopeSelect().value).toBe("all");
  });

  it("respects a pick of the reader's own", async () => {
    await render();
    await click(runRow());

    await setScope("all");
    // Re-rendering the same conversation must not take the choice back.
    await render();

    expect(scopeSelect().value).toBe("all");
  });

  it("re-asks it for whoever is answering next", async () => {
    await render();
    await click(runRow());
    await setScope("all");

    await render("sage");

    expect(scopeSelect().value).toBe("sage");
  });
});
