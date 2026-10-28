/**
 * BUG-9: the delete confirm dialog is one mutation shared across every row in
 * the rail. Cancelling a failed delete left that failure's `isError` state on
 * the mutation, so the next row's dialog opened already showing the FIRST
 * row's error — blaming a delete that was never attempted, and with the
 * wrong reason besides (the fixed sentence, not the server's own detail).
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

import { type AgentSummary } from "@/lib/api";

const deleteAgent = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { deleteAgent: (slug: string) => deleteAgent(slug) },
  CHAT_SLUG: "condor",
}));

const { ChatRail } = await import("./ChatRail");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const A = {
  slug: "brigado",
  name: "Brigado",
  description: "MM",
  status: "idle",
} as AgentSummary;

const B = {
  slug: "backpack",
  name: "Backpack",
  description: "MM",
  status: "idle",
} as AgentSummary;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  deleteAgent.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.querySelectorAll("[role='menu']").forEach((n) => n.remove());
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
            agents={[A, B]}
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

async function click(el: Element | null | undefined) {
  await act(async () => {
    el?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    // Let the mutation's (rejected or resolved) promise settle and its
    // state update flush before the assertion reads the DOM.
    await new Promise((r) => setTimeout(r, 0));
  });
}

function menuTrigger(name: string) {
  return container.querySelector<HTMLElement>(
    `[aria-label="More for ${name}"]`,
  );
}

function menuButton(label: string) {
  return Array.from(
    document.querySelectorAll<HTMLButtonElement>("[role='menu'] button"),
  ).find((b) => b.textContent?.includes(label));
}

function dialogConfirmButton() {
  return Array.from(
    document.querySelectorAll<HTMLButtonElement>("button"),
  ).find((b) => b.textContent === "Delete");
}

function dialogCancelButton() {
  return Array.from(
    document.querySelectorAll<HTMLButtonElement>("button"),
  ).find((b) => b.textContent === "Cancel");
}

async function openDeleteDialog(name: string) {
  await click(menuTrigger(name));
  await click(menuButton("Delete"));
}

describe("the agent delete dialog", () => {
  it("shows the server's own refusal, not a fixed sentence", async () => {
    deleteAgent.mockRejectedValue(
      new Error("Agent 'brigado' is shipped and cannot be deleted"),
    );
    await render();

    await openDeleteDialog("Brigado");
    await click(dialogConfirmButton());

    expect(container.textContent).toContain(
      "Agent 'brigado' is shipped and cannot be deleted",
    );
    expect(container.textContent).not.toContain("may have running loops");
  });

  it("does not carry a cancelled delete's error into the next row's dialog", async () => {
    deleteAgent.mockRejectedValue(new Error("Failed: running loops"));
    await render();

    await openDeleteDialog("Brigado");
    await click(dialogConfirmButton());
    expect(container.textContent).toContain("Failed: running loops");

    await click(dialogCancelButton());
    await openDeleteDialog("Backpack");

    expect(container.textContent).not.toContain("Failed: running loops");
  });
});
