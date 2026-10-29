/**
 * The `⋯` on an agent's rail row: its workspace, and deleting it.
 *
 * A row click has always meant "talk to this agent", which left the agent's
 * own workspace with no door from the only list that names every agent, and
 * deleting one four clicks deep behind a chat nobody wanted. These pin the two
 * things the menu has to get right: it does not start a chat underneath
 * itself, and it does not offer a delete the server is going to refuse.
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

const IDLE = {
  slug: "brigado",
  name: "Brigado",
  description: "MM",
  status: "idle",
} as AgentSummary;

const LOOPING = {
  slug: "quad",
  name: "Quad Threat",
  description: "Four at once",
  status: "running",
} as AgentSummary;

let container: HTMLDivElement;
let root: Root;
let talked: string[];

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  talked = [];
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.querySelectorAll("[role='menu']").forEach((n) => n.remove());
});

async function render(agents: AgentSummary[] = [IDLE]) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <ChatRail
            agents={[
              { slug: "condor", name: "Condor", status: "idle" } as AgentSummary,
              ...agents,
            ]}
            runningTasks={0}
            activeSlug=""
            hasSession={false}
            liveIds={new Set()}
            activeId=""
            open
            onClose={() => {}}
            onTalk={(slug) => talked.push(slug)}
            onNew={() => {}}
            onOpenConversation={() => {}}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
}

/** The row's `⋯`, by the label it is announced with. */
function menuTrigger(name: string) {
  return container.querySelector<HTMLElement>(
    `[aria-label="More for ${name}"]`,
  );
}

async function click(el: Element | null) {
  await act(async () => {
    el?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("the agent row's ⋯ menu", () => {
  it("opens the agent's workspace without starting a chat with it", async () => {
    await render();
    const trigger = menuTrigger("Brigado");
    expect(trigger).not.toBeNull();

    await click(trigger);
    // The row underneath must not have taken the click as "talk to Brigado".
    expect(talked).toEqual([]);

    const workspace = document.querySelector<HTMLAnchorElement>(
      "[role='menu'] a[href='/agents/brigado']",
    );
    expect(workspace?.textContent).toContain("Open workspace");
  });

  it("offers Delete on an idle agent", async () => {
    await render();
    await click(menuTrigger("Brigado"));
    const del = Array.from(
      document.querySelectorAll<HTMLButtonElement>("[role='menu'] button"),
    ).find((b) => b.textContent?.includes("Delete"));
    expect(del).not.toBeUndefined();
    expect(del!.disabled).toBe(false);
  });

  it("refuses Delete while the agent is looping, and says why", async () => {
    await render([LOOPING]);
    await click(menuTrigger("Quad Threat"));
    const del = Array.from(
      document.querySelectorAll<HTMLButtonElement>("[role='menu'] button"),
    ).find((b) => b.textContent?.includes("Delete"));
    expect(del?.disabled).toBe(true);
    expect(del?.textContent).toContain("stop its loops first");
  });

  it("gives Condor no menu — the store refuses to delete the default agent", async () => {
    await render();
    expect(menuTrigger("Condor")).toBeNull();
  });
});
