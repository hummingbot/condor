/**
 * The agent panel's unsaved-edit guard covers every door out of the pane
 * (CORR-395), not only the sheet's own close.
 *
 * The harness is `AgentChatTab`'s pane wiring and nothing else: `?panel=` read
 * and written through `paneUrl`, the real `AgentPanel` keyed on the slug it
 * shows, and the rail tile, desk tile, routine library and Execution row as
 * the `openPane` calls the page makes for them.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useSearchParams } from "react-router-dom";
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
      <MemoryRouter initialEntries={["/?panel=agent"]}>
        <QueryClientProvider client={client}>
          <Host />
        </QueryClientProvider>
      </MemoryRouter>,
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

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgentBrain
    .mockReset()
    .mockImplementation(async (slug: string) =>
      slug === "kraken" ? brain("kraken", "Kraken") : brain("orca", "Orca"),
    );
  getAgent.mockReset().mockResolvedValue(null);
  getDelegationHistory.mockReset().mockResolvedValue({ delegations: [] });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("a door out of a dirty agent panel", () => {
  for (const [name, lands] of [
    ["rail", "none"],
    ["desk", "desk"],
    ["routines", "routines"],
  ] as const) {
    it(`asks before the ${name} hand-off, and Discard goes through`, async () => {
      await render();
      await dirtyTheEditor();

      await click(door(name));
      expect(dialogOpen()).toBe(true);
      expect(paneKind()).toBe("agent");
      expect(textarea()!.value).toBe("an unsaved draft");

      await click(buttonNamed("Discard")!);
      expect(dialogOpen()).toBe(false);
      expect(paneKind()).toBe(lands);
      expect(textarea()).toBeNull();
    });

    it(`keeps the panel and the draft when the ${name} hand-off is cancelled`, async () => {
      await render();
      await dirtyTheEditor();

      await click(door(name));
      await click(buttonNamed("Cancel")!);
      expect(dialogOpen()).toBe(false);
      expect(paneKind()).toBe("agent");
      expect(textarea()!.value).toBe("an unsaved draft");
    });
  }

  it("asks before its own close too", async () => {
    await render();
    await dirtyTheEditor();

    await click(document.querySelector<HTMLElement>('button[title^="Close"]')!);
    expect(dialogOpen()).toBe(true);
    await click(buttonNamed("Discard")!);
    expect(paneKind()).toBe("none");
  });

  it("asks before an Execution row re-slugs it, and the next agent opens reading", async () => {
    await render();
    await dirtyTheEditor();

    await click(door("row"));
    expect(dialogOpen()).toBe(true);
    await click(buttonNamed("Discard")!);

    expect(paneKind()).toBe("agent");
    expect(getAgentBrain).toHaveBeenCalledWith("kraken");
    // Remounted on the slug: Kraken's Brain in read mode, not Orca's editor
    // left open and seeded with Kraken's text.
    expect(textarea()).toBeNull();
    expect(document.body.textContent).toContain("Kraken brain");
    expect(document.body.textContent).not.toContain("Unsaved changes.");
  });

  it("does not ask once the flag is gone, nor for a clean panel", async () => {
    await render();
    await click(door("desk"));
    expect(dialogOpen()).toBe(false);
    expect(paneKind()).toBe("desk");

    // Back to the panel, dirty it, discard via the rail: the next panel starts
    // clean, so leaving it again is one click.
    await click(door("rail"));
    await dirtyTheEditor();
    await click(door("rail"));
    await click(buttonNamed("Discard")!);
    await click(door("rail"));
    expect(paneKind()).toBe("agent");
    await click(door("desk"));
    expect(dialogOpen()).toBe(false);
    expect(paneKind()).toBe("desk");
  });
});
