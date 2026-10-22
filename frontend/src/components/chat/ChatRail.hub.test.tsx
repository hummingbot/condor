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

async function render() {
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
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
}

describe("the hub entry (FEAT-153)", () => {
  it("links to /hub, below the agent list rather than in a bar of its own", async () => {
    await render();
    const link = container.querySelector("a[href='/hub']");
    expect(link).not.toBeNull();
    expect(link?.textContent).toContain("Hub");

    // Below every agent: it comes after the last one in document order.
    const lastAgentRow = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Brigado"),
    );
    expect(lastAgentRow).not.toBeUndefined();
    expect(
      lastAgentRow!.compareDocumentPosition(link!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
