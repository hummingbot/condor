/**
 * The spine is the run's navigation, and it must not lie about the ticks.
 *
 * Four beat states, one of which exists only because of history: every session
 * written before FEAT-097 journals `actions=0` on every tick and keeps no
 * `actions.jsonl` at all. The naive reading of that is "twenty ticks that did
 * nothing", which is an assertion the data does not support — so the fourth
 * state says *unrecorded*, and this file pins it beside the three real ones.
 *
 * The other promise is the click: a beat is an address, and clicking one hands
 * up the tick so the page can put it in the URL.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentActionRow } from "@/lib/agent-attribution";
import { TickSpine } from "./TickSpine";

vi.mock("@/lib/api", () => ({
  api: {
    getSessionJournal: vi.fn(async () => ({ content: JOURNAL })),
    getSessionActions: vi.fn(async () => ({ actions: ACTIONS })),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let JOURNAL = "";
let ACTIONS: AgentActionRow[] = [];

function journal(ticks: { tick: number; actions: number; summary?: string }[]): string {
  const lines = ticks.map(
    (t) =>
      `- tick#${t.tick} | 2026-08-06 22:${String(t.tick).padStart(2, "0")} | actions=${t.actions} | ${t.summary ?? "held"}`,
  );
  return `# Journal\n\n## Ticks\n\n${lines.join("\n")}\n`;
}

function deed(over: Partial<AgentActionRow> = {}): AgentActionRow {
  return {
    tick: 1,
    at: 1_700_000_000,
    tool: "create_grid_executor",
    verb: "create_grid_executor",
    summary: "Create grid executor on SOL-USDC",
    ok: true,
    error: "",
    ...over,
  };
}

// ── Harness ──

let container: HTMLDivElement;
let root: Root;
let picked: (number | null)[];

async function render({
  hasActionsLog = true,
  selectedTick = null as number | null,
} = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <TickSpine
          slug="brigado"
          sslug="brl_mm"
          sessionNum={1}
          hasActionsLog={hasActionsLog}
          selectedTick={selectedTick}
          onSelectTick={(t) => picked.push(t)}
        />
      </QueryClientProvider>,
    );
  });
  await settle();
}

/** react-query resolves on a later macrotask than the render that asked. */
async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const beats = () => [...document.querySelectorAll<HTMLElement>("[data-beat]")];
const states = () => beats().map((b) => b.dataset.beatState);

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  JOURNAL = "";
  ACTIONS = [];
  picked = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("one beat per tick", () => {
  it("draws every tick the journal recorded, oldest first", async () => {
    JOURNAL = journal([
      { tick: 1, actions: 0 },
      { tick: 2, actions: 0 },
      { tick: 3, actions: 0 },
    ]);
    await render();

    expect(beats().map((b) => b.dataset.beat)).toEqual(["1", "2", "3"]);
  });

  it("says so rather than drawing an empty strip for a run with no ticks", async () => {
    JOURNAL = "# Journal\n";
    await render();

    expect(beats()).toHaveLength(0);
    expect(container.querySelector("[data-spine-empty]")).not.toBeNull();
  });
});

describe("what a beat's colour claims", () => {
  it("is green when the tick's deeds all worked", async () => {
    JOURNAL = journal([{ tick: 1, actions: 2 }]);
    ACTIONS = [deed({ tick: 1 }), deed({ tick: 1, verb: "stop_executor" })];
    await render();

    expect(states()).toEqual(["ok"]);
  });

  it("is red when any deed on the tick failed", async () => {
    JOURNAL = journal([{ tick: 1, actions: 2 }]);
    ACTIONS = [deed({ tick: 1 }), deed({ tick: 1, ok: false, error: "rejected" })];
    await render();

    expect(states()).toEqual(["failed"]);
  });

  it("is hollow when the run keeps a log and the tick did nothing", async () => {
    JOURNAL = journal([
      { tick: 1, actions: 1 },
      { tick: 2, actions: 0 },
    ]);
    ACTIONS = [deed({ tick: 1 })];
    await render({ hasActionsLog: true });

    expect(states()).toEqual(["ok", "idle"]);
  });

  it("never claims a pre-log run did nothing", async () => {
    // Every session on disk before FEAT-097: `actions=0` on every line and no
    // `actions.jsonl` to check it against.
    JOURNAL = journal([
      { tick: 1, actions: 0 },
      { tick: 2, actions: 0 },
      { tick: 3, actions: 0 },
    ]);
    await render({ hasActionsLog: false });

    expect(states()).toEqual(["unlogged", "unlogged", "unlogged"]);
    expect(container.textContent).toContain("no action log for this run");
  });
});

describe("a beat is an address", () => {
  it("hands up the tick it was clicked on", async () => {
    JOURNAL = journal([
      { tick: 1, actions: 0 },
      { tick: 7, actions: 0 },
    ]);
    await render();

    act(() => {
      beats()[1].click();
    });
    expect(picked).toEqual([7]);
  });

  it("goes back to the run overview", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }]);
    await render({ selectedTick: 1 });

    act(() => {
      container.querySelector<HTMLElement>("[data-spine-overview]")!.click();
    });
    expect(picked).toEqual([null]);
  });

  it("shows the deed on hover, not the model's narration", async () => {
    JOURNAL = journal([{ tick: 4, actions: 1, summary: "thinking about it" }]);
    ACTIONS = [deed({ tick: 4, summary: "Deploy bot 'brl_mm'" })];
    await render();

    expect(beats()[0].title).toBe("#4 — Deploy bot 'brl_mm'");
  });
});
