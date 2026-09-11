/**
 * The rail lends its column to the pane, and takes it back.
 *
 * Four columns — rail, transcript, pane, dock — is more chrome than any window
 * has room for, so opening a report collapses the rail to a strip of icons.
 * The cases here pin the part that is easy to get wrong: the collapse is a
 * loan, not a preference. A report must not quietly rewrite how the workspace
 * opens tomorrow, and a rail the reader collapsed by hand must stay collapsed
 * when the pane closes.
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

import { api, type AgentSummary } from "@/lib/api";
import { ChatRail } from "./ChatRail";
import { WorkspacePaneOutlet, WorkspacePaneProvider } from "./WorkspacePane";
import { WorkspaceSheet } from "./WorkspaceSheet";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const AGENTS: AgentSummary[] = [
  { slug: "condor", name: "Condor", description: "General" } as AgentSummary,
  { slug: "brigado", name: "Brigado", description: "MM" } as AgentSummary,
];

let container: HTMLDivElement;
let root: Root;

/** The workspace, reduced to the rail and whatever is in the pane. */
function Workspace({ sheet }: { sheet: boolean }) {
  return (
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <WorkspacePaneProvider>
          <div className="flex">
            <ChatRail
              agents={AGENTS}
              runningTasks={0}
              activeSlug=""
              hasSession={false}
              liveIds={new Set()}
              activeId=""
              open
              onClose={() => {}}
              onTalk={() => {}}
              onNew={() => {}}
              onOpenConversation={() => {}}
            />
            <WorkspacePaneOutlet />
            {sheet && (
              <WorkspaceSheet title="Trading Report" onClose={() => {}} bleed>
                <p>report body</p>
              </WorkspaceSheet>
            )}
          </div>
        </WorkspacePaneProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

async function render(sheet = false) {
  await act(async () => {
    root.render(<Workspace sheet={sheet} />);
  });
}

/** Expanded, the rail lists who you can talk to; collapsed, it is icons. */
const listsAgents = () => !!container.textContent?.includes("Brigado");
const expandButton = () =>
  container.querySelector<HTMLButtonElement>(
    'button[title="Show agents and conversations"]',
  );
const collapseButton = () =>
  container.querySelector<HTMLButtonElement>('button[title="Collapse"]');

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  // Wide enough to split, so a sheet claims the pane.
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
  // The conversation list fetches its own history; this test is about the rail.
  vi.spyOn(api, "listConversations").mockResolvedValue([]);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("ChatRail beside an open pane", () => {
  it("collapses while the pane is open and comes back after", async () => {
    await render();
    expect(listsAgents()).toBe(true);

    await render(true);
    expect(listsAgents()).toBe(false);
    expect(expandButton()).toBeTruthy();

    await render(false);
    expect(listsAgents()).toBe(true);
  });

  it("writes nothing down when it collapses on its own", async () => {
    await render(true);
    expect(localStorage.getItem("condor.chat.rail.open")).toBeNull();
  });

  it("gives back what it borrowed, not what it prefers", async () => {
    await render();
    // Collapsed by hand: that is a preference, and it survives the pane.
    await click(collapseButton()!);
    expect(localStorage.getItem("condor.chat.rail.open")).toBe("false");

    await render(true);
    await render(false);
    expect(listsAgents()).toBe(false);
  });

  it("can be reopened by hand while the pane is up, and stays open", async () => {
    await render(true);
    await click(expandButton()!);
    expect(listsAgents()).toBe(true);

    await render(false);
    expect(listsAgents()).toBe(true);
    expect(localStorage.getItem("condor.chat.rail.open")).toBe("true");
  });
});
