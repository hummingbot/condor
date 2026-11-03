/**
 * The tab strip ends in a `+`, and it asks who with.
 *
 * Starting a second conversation used to live only on the rail's per-agent row,
 * a column most readers keep collapsed — so the gesture the tab strip is about
 * was the one thing the tab strip could not do. These cases pin the button's
 * contract: it lists Condor apart from the specialists (Condor is bound by
 * nobody, so its slug is `""`), it hands the picked slug back, and it stays out
 * of the scroller so a busy strip cannot hide it.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatSlot } from "@/hooks/useChatSocket";
import type { AgentSummary } from "@/lib/api";

import { SessionTabs } from "./SessionTabs";

let container: HTMLDivElement;
let root: Root;

function slot(info: Partial<ChatSlot["info"]>): ChatSlot {
  return {
    info: { slot_id: "s1", agent_key: "claude_acp", ...info },
    messages: [],
  };
}

function agent(over: Partial<AgentSummary>): AgentSummary {
  return {
    slug: "x",
    name: "X",
    description: "",
    when_to_consult: "",
    agent_key: "claude_acp",
    strategy_count: 0,
    strategies: [],
    status: "idle",
    session_count: 0,
    experiment_count: 0,
    tick_count: 0,
    daily_pnl: 0,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    instances: [],
    ...over,
  };
}

const ROSTER = [
  agent({ slug: "condor", name: "Condor", description: "General assistant" }),
  agent({ slug: "brigado", name: "Brigado" }),
];

async function render(picked: Array<[string, string | null]>) {
  await act(async () => {
    root.render(
      <SessionTabs
        slots={[slot({ agent_slug: "brigado", label: "Brigado" })]}
        activeSlotId="s1"
        isSlotStreaming={() => false}
        permissionRequests={{}}
        onSelect={() => {}}
        onClose={() => {}}
        agents={ROSTER}
        onNew={(slug, a) => picked.push([slug, a?.slug ?? null])}
      />,
    );
  });
}

/** The menu is portalled into `document.body`, not into our container. */
const menuItems = () =>
  Array.from(document.body.querySelectorAll<HTMLElement>('[role="menuitem"]'));

const plus = () =>
  container.querySelector<HTMLElement>('[aria-label="New chat"]')!;

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

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

describe("SessionTabs new chat", () => {
  it("offers every agent, with Condor as the unbound one", async () => {
    const picked: Array<[string, string | null]> = [];
    await render(picked);

    expect(menuItems()).toHaveLength(0);
    await click(plus());

    const labels = menuItems().map((el) => el.textContent);
    expect(labels).toHaveLength(2);
    expect(labels[0]).toContain("Condor");
    expect(labels[1]).toContain("Brigado");
  });

  it("reports the picked agent, and Condor as the empty slug", async () => {
    const picked: Array<[string, string | null]> = [];
    await render(picked);

    await click(plus());
    await click(menuItems()[1]);
    // The specialist arrives with its slug *and* its record — the tab colours
    // the hero off the latter before a session exists.
    expect(picked).toEqual([["brigado", "brigado"]]);
    // Picking closes the menu, so the next `+` is a fresh choice.
    expect(menuItems()).toHaveLength(0);

    await click(plus());
    await click(menuItems()[0]);
    // Condor is bound by binding nobody: `""` everywhere else here too.
    expect(picked[1]).toEqual(["", null]);
  });

  it("keeps the + out of the strip that scrolls", async () => {
    await render([]);

    // The tabs scroll; the button after them must not, or a strip with enough
    // sessions open would push the one control that starts another off screen.
    const scroller = container.querySelector(".overflow-x-auto")!;
    expect(scroller.contains(plus())).toBe(false);
  });
});
