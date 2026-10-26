/**
 * The one door into Botcamp Hub (FEAT-153), below the agent list.
 *
 * It used to be a bar of its own above the conversation; this pins where it
 * landed instead — inside the rail, below every agent you can talk to, rather
 * than competing with them for the same row or sitting apart from them in a
 * strip above the whole workspace.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { type AgentSummary } from "@/lib/api";
import { ChatRail } from "./ChatRail";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const AGENTS: AgentSummary[] = [
  { slug: "condor", name: "Condor", description: "General" } as AgentSummary,
  { slug: "brigado", name: "Brigado", description: "MM" } as AgentSummary,
];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render({
  hubActive = false,
  activeSlug = "",
}: { hubActive?: boolean; activeSlug?: string } = {}) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <ChatRail
            agents={AGENTS}
            runningTasks={0}
            activeSlug={activeSlug}
            hasSession={!!activeSlug}
            hubActive={hubActive}
            liveIds={new Set()}
            activeId=""
            open
            onClose={() => {}}
            onTalk={() => {}}
            onNew={() => {}}
            onOpenConversation={() => {}}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
}

describe("the hub entry (FEAT-153)", () => {
  it("links to /hub from the foot of the rail, below the agent list", async () => {
    await render();
    const link = container.querySelector("a[href='/hub']");
    expect(link).not.toBeNull();
    expect(link?.textContent).toContain("Hub");

    // Below every agent, and pinned to the bottom rather than left to float
    // up when there are few conversations.
    const lastAgentRow = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Brigado"),
    );
    expect(lastAgentRow).not.toBeUndefined();
    expect(
      lastAgentRow!.compareDocumentPosition(link!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(link!.parentElement?.className).toContain("mt-auto");
  });

  it("lights the Hub row, and no agent's, while the Hub is in the column", async () => {
    await render({ hubActive: true, activeSlug: "brigado" });
    const link = container.querySelector("a[href='/hub']");
    expect(link?.getAttribute("aria-current")).toBe("page");
    const brigado = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Brigado"),
    );
    expect(brigado?.className).not.toContain("text-[var(--color-primary)]");
  });

  it("leaves the Hub row unlit on a conversation", async () => {
    await render({ activeSlug: "brigado" });
    const link = container.querySelector("a[href='/hub']");
    expect(link?.hasAttribute("aria-current")).toBe(false);
  });
});
