/**
 * "Browse all" — the routine library, opened beside the conversation (FEAT-077).
 *
 * The dock's Routines section shows the recent end of what this conversation
 * ran; its header is the door to all of it. What is pinned here is the wiring
 * that makes that door useful and safe: the library lands in the workspace pane
 * scoped to the agent being talked to, it is handed the conversation so a run
 * from it belongs here, and it is the pane's only claimant — a run row opened
 * behind it would stack two sheets in the same 550px.
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
const browseAll = () =>
  [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Browse all"),
  )!;

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
  });

  it("is the pane's only claimant", async () => {
    await render();

    // A run row opened behind the library would be a second sheet in the pane.
    await click(browseAll());
    await click(runRow());
    expect(library()).toBeNull();
    expect(pane().textContent).toContain("Alpha Check");

    // And the library opened over a run row replaces it, rather than stacking.
    await click(browseAll());
    expect(pane().contains(library())).toBe(true);
  });
});
