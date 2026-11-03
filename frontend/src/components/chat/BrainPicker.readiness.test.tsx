/**
 * A model this machine cannot run is shown as unpickable, with the fix on it.
 *
 * Picking one used to spawn a session that could not start, and because the
 * respawn tears the old subprocess down first, the failure took the chat's
 * session with it — the next pick came back "No session <key>". The backend
 * now says which providers are runnable (`/sessions/options`), and the row is
 * where that belongs: a reason before the pick beats a banner after it.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatAgentOption } from "@/lib/api";
import { BrainPicker } from "./BrainPicker";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const AGENTS: ChatAgentOption[] = [
  { key: "claude-acp:sonnet", label: "Claude (ACP) — Sonnet", ready: true },
  {
    key: "ollama:",
    label: "Ollama — Default Model",
    ready: false,
    detail:
      "not reachable at http://localhost:11434/v1 — start it with `ollama serve`",
  },
  // No readiness at all: the backend could not probe, so it must still be
  // offered rather than silently withheld.
  { key: "codex", label: "ChatGPT Codex" },
];

let container: HTMLDivElement;
let root: Root;
let picked: string[];

const rowFor = (label: string) =>
  Array.from(document.querySelectorAll("button")).find((b) =>
    b.textContent?.startsWith(label),
  );

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

async function openMenu() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <BrainPicker
          agents={AGENTS}
          selectedAgentKey="claude-acp:sonnet"
          onSelect={(sel) => picked.push(sel.agentKey ?? "")}
        />
      </QueryClientProvider>,
    );
  });
  await click(container.querySelector("button")!);
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  picked = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("BrainPicker readiness", () => {
  it("disables a model that cannot run and shows why", async () => {
    await openMenu();

    const row = rowFor("Ollama — Default Model");
    expect(row).toBeTruthy();
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row!.textContent).toContain("ollama serve");

    await click(row!);
    expect(picked).toEqual([]);
  });

  it("leaves a ready model, and an unprobed one, pickable", async () => {
    await openMenu();

    expect((rowFor("ChatGPT Codex") as HTMLButtonElement).disabled).toBe(false);
    await click(rowFor("ChatGPT Codex")!);
    expect(picked).toEqual(["codex"]);
  });
});
