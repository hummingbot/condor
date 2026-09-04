/**
 * What the session's Actions block promises (FEAT-097).
 *
 * Three things are load-bearing and none of them are visual. It renders the
 * *deed* — a tool call that ran — from structured rows, so nothing in its path
 * runs a regex over markdown the way the Decisions block below it must. A call
 * that failed says so rather than reading as a success, which is the whole
 * reason the record stores an outcome at all. And a row is a way *into* the
 * tick: clicking it hands the reviewer the tick number the deed happened on,
 * which is what collapses the walk from the fleet band to a full snapshot.
 *
 * The fourth is the empty state's, and it is the honest one: a session that ran
 * before the log existed is not backfilled, so it must read as "nothing
 * recorded" and never as an error or a spinner that never resolves.
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
import { SessionActions } from "./SessionActions";

vi.mock("@/lib/api", () => ({
  api: {
    getSessionActions: vi.fn(async (...args: unknown[]) => {
      ASKED.push(args);
      return { actions: ROWS };
    }),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let ROWS: AgentActionRow[] = [];
let ASKED: unknown[][] = [];

function row(over: Partial<AgentActionRow> = {}): AgentActionRow {
  return {
    tick: 212,
    at: 1_700_000_000,
    tool: "create_grid_executor",
    verb: "create_grid_executor",
    summary: "Create grid executor on SOL-USDC for 100 quote",
    ok: true,
    error: "",
    ...over,
  };
}

// ── Harness ──

let container: HTMLDivElement;
let root: Root;
let clicked: number[];

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <SessionActions
          slug="brigado"
          sslug="brl_mm"
          sessionNum={7}
          onSnapshotClick={(tick) => clicked.push(tick)}
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

const rows = () => [...document.querySelectorAll<HTMLElement>("[data-action-row]")];
const verbs = () => rows().map((r) => r.dataset.actionRow);

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  ROWS = [];
  ASKED = [];
  clicked = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the deed, not the narration", () => {
  it("lists what the session did, oldest-last, as the log stored it", async () => {
    ROWS = [
      row({ tick: 210, verb: "manage_bots:deploy", summary: "Deploy bot 'brl_mm'" }),
      row({ tick: 212 }),
    ];
    await render();

    expect(verbs()).toEqual(["manage_bots:deploy", "create_grid_executor"]);
    // The summary is rendered once, in Python, by the confirmation prompt's own
    // renderer — the block prints it and interprets no arguments of its own.
    expect(container.textContent).toContain("Deploy bot 'brl_mm'");
    expect(container.textContent).toContain("Create grid executor on SOL-USDC");
    expect(container.textContent).toContain("#210");
  });

  it("asks the session it was given", async () => {
    ROWS = [row()];
    await render();

    expect(ASKED[0].slice(0, 3)).toEqual(["brigado", "brl_mm", 7]);
  });

  it("says a failed call failed, and why", async () => {
    ROWS = [
      row({
        tick: 213,
        tool: "stop_executor",
        verb: "stop_executor",
        summary: "Stop executor a1b2c3d4e5f6...",
        ok: false,
        error: "ToolError: executor is not running",
      }),
    ];
    await render();

    expect(rows()[0].dataset.actionOk).toBe("false");
    expect(container.textContent).toContain("Failed");
    expect(container.textContent).toContain("executor is not running");
  });

  it("marks a refused call as not-ok rather than inventing a success", async () => {
    ROWS = [row({ ok: false, error: "refused" })];
    await render();

    expect(rows()[0].dataset.actionOk).toBe("false");
    expect(container.textContent).toContain("refused");
  });

  it("hands the reviewer the tick a deed happened on", async () => {
    ROWS = [row({ tick: 212 }), row({ tick: 214 })];
    await render();

    await act(async () => {
      rows()[1].click();
    });

    expect(clicked).toEqual([214]);
  });

  it("reads as nothing recorded for a session that never acted", async () => {
    ROWS = [];
    await render();

    expect(rows()).toHaveLength(0);
    expect(document.querySelector("[data-action-empty]")).not.toBeNull();
  });
});
