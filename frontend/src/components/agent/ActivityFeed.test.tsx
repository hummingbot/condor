/**
 * What the Activity feed promises (FEAT-058).
 *
 * Four things are load-bearing and none of them are visual: both kinds of run
 * appear in one timeline and are told apart; each row says the one thing its
 * kind actually knows (who asked, for a consult; how much work it did, for a
 * background task) and invents nothing for the other; the summary strip names
 * the sample it measured rather than implying it speaks for the whole history;
 * and a record written before kinds existed still reads as a delegation.
 *
 * The fifth is the dock's: it asks for background tasks only, which is the
 * regression this feature could most easily have introduced — filling the chat
 * dock with every consult the conversation made.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DelegationSummary } from "@/lib/api";
import { ActivityFeed } from "./ActivityFeed";

vi.mock("@/lib/api", () => ({
  api: {
    getDelegationHistory: vi.fn(
      async (agent?: string, limit?: number, kind?: string) => {
        ASKED.push({ agent, limit, kind });
        return { delegations: ROWS };
      },
    ),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let ROWS: DelegationSummary[] = [];
let ASKED: { agent?: string; limit?: number; kind?: string }[] = [];

// ── Fixtures ──

// Anchored to the clock the component reads, so a row's elapsed time renders as
// the minutes it is rather than the years since a hardcoded epoch.
const NOW = Math.floor(Date.now() / 1000);

function run(over: Partial<DelegationSummary> & { task_id: string }): DelegationSummary {
  return {
    agent: "scout",
    user_id: 7,
    chat_id: 42,
    server_name: null,
    task: "what is the funding on HYPE right now",
    status: "done",
    kind: "consult",
    caller: "condor",
    conversation_id: "",
    started_at: NOW - 60,
    ended_at: NOW - 48,
    ...over,
  };
}

// ── Harness ──

let container: HTMLDivElement;
let root: Root;

async function render(props: { agent?: string; kind?: "delegate" | "consult" } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ActivityFeed {...props} />
      </QueryClientProvider>,
    );
  });
  // react-query resolves on a later macrotask than the render that asked.
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const rows = () => [...document.querySelectorAll<HTMLElement>("[data-activity-row]")];
const kinds = () => rows().map((r) => r.dataset.activityRow);
const summary = () =>
  document.querySelector<HTMLElement>("[data-activity-summary]")!.textContent!;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  ROWS = [];
  ASKED = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("one timeline, two kinds", () => {
  it("lists background tasks and consults together, told apart by kind", async () => {
    ROWS = [
      run({ task_id: "a", kind: "delegate", task: "back-test the SOL grid" }),
      run({ task_id: "b", kind: "consult" }),
    ];
    await render({ agent: "scout" });

    expect(kinds()).toEqual(["delegate", "consult"]);
  });

  it("names the caller on a consult and the tool count on a background task", async () => {
    ROWS = [
      run({ task_id: "a", kind: "delegate", tool_count: 22, caller: "" }),
      run({ task_id: "b", kind: "consult", caller: "condor", tool_count: undefined }),
    ];
    await render({ agent: "scout" });

    const [delegation, consult] = rows();
    expect(delegation.textContent).toContain("background");
    expect(delegation.querySelector("[data-tool-count]")!.textContent).toBe("22");
    expect(consult.textContent).toContain("asked by condor");
    // A consult has no tool count, so the cell is absent — never a zero.
    expect(consult.querySelector("[data-tool-count]")).toBeNull();
  });

  it("says a consult with no caller was asked by you, not by nobody", async () => {
    ROWS = [run({ task_id: "a", kind: "consult", caller: "" })];
    await render({ agent: "scout" });

    expect(rows()[0].textContent).toContain("asked by you");
  });

  it("reads a record written before kinds existed as a background task", async () => {
    ROWS = [run({ task_id: "a", kind: undefined, caller: undefined, tool_count: 3 })];
    await render({ agent: "scout" });

    expect(kinds()).toEqual(["delegate"]);
    expect(rows()[0].textContent).toContain("background");
  });
});

describe("the summary strip", () => {
  it("names the sample it measured rather than the whole history", async () => {
    ROWS = [run({ task_id: "a" }), run({ task_id: "b" })];
    await render({ agent: "scout" });

    expect(summary()).toContain("2 runs");
    expect(summary()).toContain("last 2 shown");
  });

  it("measures the share and the median over what actually finished", async () => {
    ROWS = [
      run({ task_id: "a", status: "done", started_at: NOW - 20, ended_at: NOW - 10 }),
      run({ task_id: "b", status: "error", started_at: NOW - 60, ended_at: NOW - 30 }),
      // Still running: no outcome and no duration to fold in yet.
      run({ task_id: "c", status: "running", ended_at: undefined }),
    ];
    await render({ agent: "scout" });

    expect(summary()).toContain("3 runs");
    expect(summary()).toContain("50% done"); // 1 of the 2 that finished
    expect(summary()).toContain("median 30s");
  });

  it("omits a share and a median it has nothing to measure", async () => {
    ROWS = [run({ task_id: "a", status: "running", ended_at: undefined })];
    await render({ agent: "scout" });

    expect(summary()).toContain("1 run");
    expect(summary()).not.toContain("% done");
    expect(summary()).not.toContain("median");
  });
});

describe("scoping", () => {
  it("asks for every kind on an agent's page", async () => {
    ROWS = [run({ task_id: "a" })];
    await render({ agent: "scout" });

    expect(ASKED[0]).toEqual({ agent: "scout", limit: 100, kind: undefined });
  });

  it("asks for background tasks only when the dock pins it", async () => {
    ROWS = [run({ task_id: "a", kind: "delegate" })];
    await render({ kind: "delegate" });

    expect(ASKED[0]).toEqual({ agent: undefined, limit: 100, kind: "delegate" });
  });

  it("says the feed is empty rather than rendering nothing", async () => {
    ROWS = [];
    await render({ agent: "scout" });

    expect(rows()).toHaveLength(0);
    expect(container.textContent).toContain("has not run yet");
  });
});
