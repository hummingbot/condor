/**
 * Browser Back and Forward out of a dirty agent panel ask first (CORR-432).
 *
 * The same harness as `usePaneGuard.test.tsx` — `AgentChatTab`'s pane wiring
 * and the real `AgentPanel` — but under a `BrowserRouter`, because what is
 * pinned here is the guard's `popstate` listener racing the router's own on
 * jsdom's real `window.history`.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { BrowserRouter, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentBrain } from "@/lib/api";

const getAgentBrain = vi.fn();
const getAgent = vi.fn();
const getDelegationHistory = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...a: unknown[]) => getAgentBrain(...a),
    getAgent: (...a: unknown[]) => getAgent(...a),
    getDelegationHistory: (...a: unknown[]) => getDelegationHistory(...a),
  },
  CHAT_SLUG: "condor",
}));

const { AgentPanel } = await import("./AgentPanel");
const { readPane, writePane } = await import("./paneUrl");
const { usePaneGuard } = await import("./usePaneGuard");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function brain(slug: string, name: string): AgentBrain {
  return {
    slug,
    name,
    description: "",
    agent_md: `# ${name} brain`,
    agent_key: "claude-code",
    when_to_consult: "",
    server_required: false,
    server_name: "",
    tools: [],
    tools_unrestricted: true,
    skills: [],
    skill_proposal: null,
    memories: [],
    routines: [],
    strategies: [],
  };
}

/** The conversation's agent, the one a bare `?panel=agent` means. */
const PANEL_SLUG = "orca";

/** Every `openPane` the host was handed, one per commit (PERF-393). */
const seenOpenPane: unknown[] = [];

function Host() {
  const [searchParams, setSearchParams] = useSearchParams();
  const pane = readPane(searchParams, {});
  const openSlug = (pane?.kind === "agent" && pane.slug) || PANEL_SLUG;
  const guard = usePaneGuard({
    pane,
    panelSlug: PANEL_SLUG,
    apply: (next) => setSearchParams(writePane(searchParams, next)),
  });
  const openPane = guard.openPane;
  useEffect(() => {
    seenOpenPane.push(guard.openPane);
  });
  return (
    <>
      <button
        data-door="rail"
        onClick={() =>
          openPane(pane?.kind === "agent" ? null : { kind: "agent" })
        }
      />
      <button data-door="desk" onClick={() => openPane({ kind: "desk" })} />
      <button
        data-door="routines"
        onClick={() => openPane({ kind: "routines", focus: {} })}
      />
      <button
        data-door="row"
        onClick={() => openPane({ kind: "agent", slug: "kraken" })}
      />
      <span data-pane>{pane?.kind ?? "none"}</span>
      {pane?.kind === "agent" && (
        <AgentPanel
          key={openSlug}
          slug={openSlug}
          name={openSlug}
          wiring={null}
          tab={pane.tab}
          onTabChange={(t) => openPane({ ...pane, tab: t })}
          onOpenRoutine={() => {}}
          onOpenStrategy={() => {}}
          onAskAgent={() => {}}
          onDirtyChange={guard.onPanelDirtyChange}
          onClose={() => openPane(null)}
        />
      )}
      {guard.dialog}
    </>
  );
}

let container: HTMLDivElement;
let root: Root;

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <BrowserRouter>
        <QueryClientProvider client={client}>
          <Host />
        </QueryClientProvider>
      </BrowserRouter>,
    );
  });
  await settle();
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await settle();
}

const door = (name: string) =>
  document.querySelector<HTMLElement>(`[data-door="${name}"]`)!;
const paneKind = () => document.querySelector("[data-pane]")!.textContent;
const textarea = () => document.querySelector("textarea");
const buttonNamed = (label: string) =>
  [...document.querySelectorAll<HTMLButtonElement>("button")].find(
    (b) => b.textContent?.trim() === label,
  );
const dialogOpen = () =>
  (document.body.textContent ?? "").includes("Discard changes?");

async function type(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle();
}

/** Open the Brain editor and leave unsaved text in it. */
async function dirtyTheEditor() {
  await click(buttonNamed("Edit")!);
  await type(textarea()!, "an unsaved draft");
  expect(document.body.textContent).toContain("Unsaved changes.");
}

/** A browser traversal, and the popstates it (and any undo) fires, settled. */
async function traverse(delta: number) {
  await act(async () => {
    window.history.go(delta);
  });
  await settle();
  await settle();
}

const search = () => new URLSearchParams(window.location.search);

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  seenOpenPane.length = 0;
  // A fresh entry to start from, so each test's Back has somewhere to land
  // that is not the previous test's pane.
  window.history.pushState(null, "", "/");
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgentBrain
    .mockReset()
    .mockImplementation(async () => brain("orca", "Orca"));
  getAgent.mockReset().mockResolvedValue(null);
  getDelegationHistory.mockReset().mockResolvedValue({ delegations: [] });
  await render();
  // The rail opens the panel: one pushed entry, `?panel=agent`.
  await click(door("rail"));
  expect(paneKind()).toBe("agent");
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("browser Back out of the agent panel", () => {
  it("closes a clean panel at once, as it always has", async () => {
    await traverse(-1);
    expect(dialogOpen()).toBe(false);
    expect(paneKind()).toBe("none");
    expect(search().get("panel")).toBeNull();
  });

  it("asks first when an editor holds a draft, and Cancel keeps both", async () => {
    await dirtyTheEditor();
    const length = window.history.length;

    await traverse(-1);
    expect(dialogOpen()).toBe(true);
    expect(paneKind()).toBe("agent");
    expect(textarea()!.value).toBe("an unsaved draft");
    // The move was undone, not papered over with a pushed entry.
    expect(search().get("panel")).toBe("agent");
    expect(window.history.length).toBe(length);

    await click(buttonNamed("Cancel")!);
    expect(dialogOpen()).toBe(false);
    expect(paneKind()).toBe("agent");
    expect(textarea()!.value).toBe("an unsaved draft");

    // Asked again on the next Back, not waved through.
    await traverse(-1);
    expect(dialogOpen()).toBe(true);
    expect(paneKind()).toBe("agent");
  });

  it("goes where Back was going on Discard, and the next Back is plain", async () => {
    await dirtyTheEditor();
    const length = window.history.length;

    await traverse(-1);
    await click(buttonNamed("Discard")!);
    await settle();
    expect(dialogOpen()).toBe(false);
    expect(paneKind()).toBe("none");
    expect(search().get("panel")).toBeNull();
    expect(window.history.length).toBe(length);

    // Forward reopens the panel clean; it can be left without a question.
    await traverse(1);
    expect(paneKind()).toBe("agent");
    expect(textarea()).toBeNull();
    await traverse(-1);
    expect(dialogOpen()).toBe(false);
    expect(paneKind()).toBe("none");
  });

  it("guards Forward to an entry that drops the panel too", async () => {
    // panel → desk, then Back to a clean panel, dirty it, press Forward.
    await click(door("desk"));
    expect(paneKind()).toBe("desk");
    await traverse(-1);
    expect(paneKind()).toBe("agent");
    await dirtyTheEditor();

    await traverse(1);
    expect(dialogOpen()).toBe(true);
    expect(paneKind()).toBe("agent");
    expect(textarea()!.value).toBe("an unsaved draft");

    await click(buttonNamed("Discard")!);
    await settle();
    expect(paneKind()).toBe("desk");
    expect(search().get("panel")).toBe("desk");
  });
});
