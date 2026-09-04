/**
 * What the Activity feed promises (FEAT-058, FEAT-061).
 *
 * Four things are load-bearing and none of them are visual: every kind of run
 * appears in one timeline and they are told apart; each row says the one thing
 * its kind actually knows (who asked, for a consult; how much work it did, for
 * a background task; how long it took, for a code run) and invents nothing for
 * the others; the summary strip names the sample it measured rather than
 * implying it speaks for the whole history; and a record written before kinds
 * existed still reads as a delegation.
 *
 * The fifth is the filter's, and it is the reason the filter is server-side: a
 * narrowed feed must *re-ask*, because filtering the page already fetched would
 * show three consults and imply that is all there ever were.
 *
 * The sixth is the dock's: it asks for background tasks only, which is the
 * regression these features could most easily have introduced — filling the
 * chat dock with every consult the conversation made, or every snippet it ran.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DelegationKind, DelegationSummary } from "@/lib/api";
import { ActivityFeed } from "./ActivityFeed";

vi.mock("@/lib/api", () => ({
  api: {
    getDelegationHistory: vi.fn(
      async (agent?: string, limit?: number, kind?: string) => {
        ASKED.push({ agent, limit, kind });
        // The filter is server-side, so the fake answers the question it was
        // asked rather than handing back the whole set every time.
        const shown = kind
          ? ROWS.filter((r) => (r.kind ?? "delegate") === kind)
          : ROWS;
        return { delegations: shown };
      },
    ),
    getCodeRun: vi.fn(async () => ({})),
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

async function render(props: { agent?: string; kind?: DelegationKind } = {}) {
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

async function clickFilter(kind: DelegationKind | "all") {
  await act(async () => {
    document
      .querySelector<HTMLElement>(`[data-activity-filter="${kind}"]`)!
      .click();
  });
  await settle();
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

describe("one timeline, three kinds", () => {
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

describe("code runs are work too", () => {
  const codeRun = (over: Partial<DelegationSummary> = {}) =>
    run({
      task_id: "c",
      kind: "code",
      task: "returns of SOL 1h",
      caller: "",
      // 340ms, the duration the store measured.
      started_at: NOW - 12,
      ended_at: NOW - 12 + 0.34,
      ...over,
    });

  it("lists a snippet beside the other two kinds", async () => {
    ROWS = [
      run({ task_id: "a", kind: "delegate" }),
      run({ task_id: "b", kind: "consult" }),
      codeRun(),
    ];
    await render({ agent: "scout" });

    expect(kinds()).toEqual(["delegate", "consult", "code"]);
  });

  it("says how long the snippet took, and nothing it cannot know", async () => {
    ROWS = [codeRun()];
    await render({ agent: "scout" });

    const row = rows()[0];
    expect(row.textContent).toContain("code");
    expect(row.querySelector("[data-code-duration]")!.textContent).toBe("340ms");
    // No caller and no tool count: those belong to the other two kinds.
    expect(row.textContent).not.toContain("asked by");
    expect(row.querySelector("[data-tool-count]")).toBeNull();
  });

  it("keeps a timeout apart from an error", async () => {
    ROWS = [
      codeRun({ task_id: "c", status: "timeout" }),
      codeRun({ task_id: "d", status: "error" }),
    ];
    await render({ agent: "scout" });

    const [timedOut, failed] = rows();
    expect(timedOut.textContent).toContain("timeout");
    expect(failed.textContent).toContain("error");
    expect(timedOut.textContent).not.toContain("error");
  });

  it("folds a snippet's real duration into the median", async () => {
    ROWS = [codeRun({ started_at: NOW - 30, ended_at: NOW - 20 })];
    await render({ agent: "scout" });

    expect(summary()).toContain("median 10s");
  });
});

describe("the kind filter", () => {
  const mixed = () => [
    run({ task_id: "a", kind: "delegate" }),
    run({ task_id: "b", kind: "consult" }),
    run({ task_id: "c", kind: "code" }),
  ];

  it("re-asks the server rather than narrowing the page it already has", async () => {
    ROWS = mixed();
    await render({ agent: "scout" });
    await clickFilter("consult");

    expect(ASKED.map((a) => a.kind)).toEqual([undefined, "consult"]);
    expect(kinds()).toEqual(["consult"]);
  });

  it("offers every kind, and All to come back to", async () => {
    ROWS = mixed();
    await render({ agent: "scout" });
    await clickFilter("code");
    expect(kinds()).toEqual(["code"]);

    await clickFilter("all");
    expect(kinds()).toEqual(["delegate", "consult", "code"]);
    expect(ASKED.map((a) => a.kind)).toEqual([undefined, "code", undefined]);
  });

  it("stays on screen when the kind it narrowed to is empty", async () => {
    // Otherwise a reader who picks a kind with no rows has no way back.
    ROWS = [run({ task_id: "a", kind: "delegate" })];
    await render({ agent: "scout" });
    await clickFilter("code");

    expect(rows()).toHaveLength(0);
    expect(document.querySelector("[data-activity-filters]")).not.toBeNull();
  });

  it("still names the sample it measured after a filter", async () => {
    ROWS = mixed();
    await render({ agent: "scout" });
    await clickFilter("consult");

    expect(summary()).toContain("1 run");
    expect(summary()).toContain("last 1 shown");
  });

  it("is absent when the dock has pinned the feed to one kind", async () => {
    ROWS = [run({ task_id: "a", kind: "delegate" })];
    await render({ kind: "delegate" });

    expect(document.querySelector("[data-activity-filters]")).toBeNull();
  });

  it("never lets a code run reach the pinned dock", async () => {
    ROWS = [
      run({ task_id: "a", kind: "delegate" }),
      run({ task_id: "c", kind: "code" }),
    ];
    await render({ kind: "delegate" });

    expect(kinds()).toEqual(["delegate"]);
    expect(ASKED).toEqual([{ agent: undefined, limit: 100, kind: "delegate" }]);
  });
});
