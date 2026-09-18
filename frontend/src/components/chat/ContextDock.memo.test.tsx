/**
 * The context dock holds still while an answer streams (PERF-394).
 *
 * `AgentChatTab` re-renders on every 50 ms flush of a streaming answer, while
 * the dock's Tasks and Routines lists move only on a 5 s / 15 s poll. So the
 * dock is memoised, and everything the page hands it — the `useContextPanels`
 * result, the delegation list, the run context and the library callback — has
 * to keep its identity across a flush, or the memo is decoration and both
 * panes re-sort and reconcile every row twenty times a second.
 *
 * What is pinned: an unrelated re-render of the host commits neither pane; a
 * new delegation list, a new agent or an opened library still does; and the
 * hook's result is one identity across renders with unchanged inputs, on a
 * narrow window too, where the disabled queries' defaults used to be fresh
 * arrays.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Delegation } from "@/lib/api";
import type { LibraryFocus } from "./DockRoutines";
import { WorkspacePaneOutlet, WorkspacePaneProvider } from "./WorkspacePane";

vi.mock("@/lib/api", () => ({
  api: {
    getRoutineInstances: () => Promise.resolve([]),
    getRoutines: () => Promise.resolve([]),
    getReports: () => Promise.resolve({ reports: [] }),
  },
}));

vi.mock("@/components/routines/ReportBrowser", () => ({
  ReportBrowser: () => <div data-testid="library" />,
}));

/** Each pane, reduced to a counter of its commits and the last list it got. */
const renders = { tasks: 0, routines: 0 };
let tasksDelegations: Delegation[] | null = null;

vi.mock("./DockTasks", () => ({
  DockTasks: (props: { delegations: Delegation[] }) => {
    renders.tasks++;
    tasksDelegations = props.delegations;
    return <div data-testid="dock-tasks" />;
  },
}));

vi.mock("./DockRoutines", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./DockRoutines")>()),
  DockRoutines: () => {
    renders.routines++;
    return <div data-testid="dock-routines" />;
  },
}));

/** Wide by default so both panes open; the identity test narrows it. */
let wide = true;
window.matchMedia = ((media: string) => ({
  matches: wide,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

const { ContextDock } = await import("./ContextDock");
const { useContextPanels } = await import("./contextPanels");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const CONVERSATION = "conv-1";
const RUN_CONTEXT = {
  serverName: "chat-server",
  sessionKey: "web:42:main",
  agentSlug: "scout",
};

function delegation(task_id: string, started_at: number): Delegation {
  return {
    task_id,
    agent: "sage",
    user_id: 42,
    chat_id: 42,
    server_name: null,
    task: `task ${task_id}`,
    status: "running",
    result: "",
    error: "",
    conversation_id: CONVERSATION,
    started_at,
  };
}

const DELEGATIONS = [
  delegation("t1", 100),
  delegation("t2", 200),
  delegation("t3", 300),
];

/** The host's levers, set by whichever `Workspace` is mounted. */
let flush: () => void = () => {};
let setDelegations: (d: Delegation[]) => void = () => {};
let setAgentSlug: (s: string) => void = () => {};
let setLibrary: (f: LibraryFocus | null) => void = () => {};
let hostRenders = 0;

/**
 * The page around the dock, wired exactly as `AgentChatTab` wires it: the
 * panels hook, then the dock, with an identity-stable run context and library
 * callback. The `tick` state is a stream flush — a re-render of the page that
 * changes nothing the dock reads.
 */
function Workspace() {
  const [, setTick] = useState(0);
  const [delegations, setDels] = useState<Delegation[]>(DELEGATIONS);
  const [agentSlug, setSlug] = useState("scout");
  const [library, setLib] = useState<LibraryFocus | null>(null);
  // After every commit, not during render: the harness's levers and counter
  // are side effects the compiler's purity rules keep out of the body.
  useEffect(() => {
    hostRenders++;
  });
  useEffect(() => {
    flush = () => setTick((t) => t + 1);
    setDelegations = setDels;
    setAgentSlug = setSlug;
    setLibrary = setLib;
  }, []);
  const panels = useContextPanels({
    delegations,
    conversationId: CONVERSATION,
    agentSlug,
    libraryOpen: !!library,
  });
  return (
    <WorkspacePaneProvider>
      <WorkspacePaneOutlet />
      <ContextDock
        panels={panels}
        delegations={delegations}
        conversationId={CONVERSATION}
        agentSlug={agentSlug}
        agentName="Scout"
        runContext={RUN_CONTEXT}
        library={library}
        onLibraryChange={setLib}
      />
    </WorkspacePaneProvider>
  );
}

let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;

async function settle() {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function mount(node: React.ReactNode) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>{node}</QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  wide = true;
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  localStorage.clear();
  renders.tasks = 0;
  renders.routines = 0;
  tasksDelegations = null;
  hostRenders = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("the context dock across a stream flush (PERF-394)", () => {
  it("commits neither pane when the page re-renders for nothing it reads", async () => {
    await mount(<Workspace />);
    expect(container.querySelector('[data-testid="dock-tasks"]')).toBeTruthy();
    expect(
      container.querySelector('[data-testid="dock-routines"]'),
    ).toBeTruthy();
    const before = { ...renders };
    const hostBefore = hostRenders;

    for (let i = 0; i < 20; i++) {
      await act(async () => flush());
    }

    // The page did re-render twenty times — the dock did not.
    expect(hostRenders).toBe(hostBefore + 20);
    expect(renders).toEqual(before);
  });

  it("re-renders for a new delegation list, a new agent and an opened library", async () => {
    await mount(<Workspace />);

    const polled = [...DELEGATIONS, delegation("t4", 400)];
    let before = renders.tasks;
    await act(async () => setDelegations(polled));
    expect(renders.tasks).toBe(before + 1);
    expect(tasksDelegations).toBe(polled);

    before = renders.tasks;
    await act(async () => setAgentSlug("sage"));
    expect(renders.tasks).toBeGreaterThan(before);

    before = renders.tasks;
    await act(async () => setLibrary({ source: "sage/funding_watch" }));
    await settle();
    expect(renders.tasks).toBeGreaterThan(before);
    expect(document.querySelector('[data-testid="library"]')).toBeTruthy();
  });
});

describe("useContextPanels' identity (PERF-394)", () => {
  type Result = ReturnType<typeof useContextPanels>;
  const seen: Result[] = [];
  let bump: () => void = () => {};

  function Probe() {
    const [, setTick] = useState(0);
    const result = useContextPanels({
      delegations: DELEGATIONS,
      conversationId: CONVERSATION,
      agentSlug: "scout",
      libraryOpen: false,
    });
    useEffect(() => {
      bump = () => setTick((t) => t + 1);
      seen.push(result);
    });
    return null;
  }

  beforeEach(() => {
    seen.length = 0;
  });

  it.each([
    ["a wide window, with both queries enabled", true],
    ["a narrow window, with both queries disabled", false],
  ])("is one result on %s", async (_label, isWide) => {
    wide = isWide;
    await mount(<Probe />);
    const settled = seen[seen.length - 1];
    expect(settled.shown).toHaveLength(isWide ? 2 : 0);

    await act(async () => bump());

    const next = seen[seen.length - 1];
    expect(seen.length).toBeGreaterThan(1);
    expect(next).toBe(settled);
    expect(next.railItems).toBe(settled.railItems);
    expect(next.toggle).toBe(settled.toggle);
    expect(next.closeAll).toBe(settled.closeAll);
    expect(next.instances).toBe(settled.instances);
    expect(next.routines).toBe(settled.routines);
  });

  it("is a new result once a pane actually toggles", async () => {
    await mount(<Probe />);
    const settled = seen[seen.length - 1];

    await act(async () => settled.toggle("tasks"));

    const next = seen[seen.length - 1];
    expect(next).not.toBe(settled);
    expect(next.shown).toEqual(["routines"]);
    expect(next.railItems).not.toBe(settled.railItems);
    expect(next.toggle).toBe(settled.toggle);
  });
});
