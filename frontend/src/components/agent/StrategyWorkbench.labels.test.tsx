/**
 * Every action in the workbench header says its name.
 *
 * They did not. `labelClass` was `dense ? "hidden" : "hidden sm:inline"`, so in
 * the chat's pane — the surface where a strategy is opened most — the header
 * was four unlabelled glyphs: a document, a stack, a scroll and a bin. The only
 * way to find out what one did was to hover it, or press it.
 *
 * A `title` is not a fix. It is hover-only, so it does not exist on touch, it
 * does not exist for anyone scanning the row, and it is invisible to the reader
 * deciding whether the stack icon is "view the fleet" or "duplicate this". The
 * icons are not a vocabulary anyone agreed to learn.
 *
 * This file exists because that is a cheap regression to reintroduce — one
 * ternary — and an expensive one to notice.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

// The workbench pulls in the playbook editor, which reads the theme at module
// load. jsdom has no `matchMedia`, so it has to exist before the import below.
window.matchMedia = ((media: string) => ({
  matches: false,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

vi.mock("@/lib/api", () => ({
  api: {
    getStrategy: vi.fn(async () => ({
      slug: "fleet_op",
      name: "PMM King BTC-BRL Fleet Operator",
      description: "Hourly autonomous operator.",
      status: "stopped",
      agent_id: "brigado.fleet_op_1",
      config: { server_name: "brigado_2", frequency_sec: 3600 },
      default_trading_context: "",
      instances: [],
      sessions: [],
      experiments: [],
      strategy_md: "",
      learnings: "",
    })),
    getAgentRuns: vi.fn(async () => []),
    getStrategySessionExecutors: vi.fn(async () => ({ performance: null })),
    getRoutineInstances: vi.fn(async () => []),
    getServers: vi.fn(async () => []),
    getStrategyPerformance: vi.fn(async () => null),
  },
}));

vi.mock("@/hooks/useAgentExecutors", () => ({
  useAgentExecutors: () => ({ executors: [] }),
}));

const { StrategyWorkbench } = await import("./StrategyWorkbench");

vi.mock("@/hooks/useFleetData", () => ({
  useFleetData: () => ({
    controllers: [],
    owners: [],
    bots: [],
    executors: [],
    paging: {},
    snapshots: [],
    truncated: false,
    runs: [],
    terminatedControllers: [],
    deeds: null,
    convert: (v: number) => v,
    currencySymbol: "$",
    rateFormatPnl: String,
    rateFormatValue: String,
    rateFormatDetailed: String,
    isLoading: false,
    error: null,
    serverOnline: true,
  }),
}));

let host: HTMLDivElement;
let root: Root;

async function render(dense: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <StrategyWorkbench
            slug="brigado"
            sslug="fleet_op"
            dense={dense}
            onDeleted={() => {}}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // Let the strategy query settle so the header renders at all — the
  // workbench draws a spinner until it does, and a header with no buttons in
  // it would pass every assertion below for the wrong reason.
  for (let i = 0; i < 20 && host.querySelectorAll("button").length === 0; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

/**
 * Text of every header action, as a reader would see it without hovering.
 *
 * `textContent` alone is not enough and the difference is the whole point:
 * jsdom loads no stylesheet, so a `<span class="hidden">Playbook</span>` — the
 * exact shape this change removed — still reads back as "Playbook" and every
 * assertion below would pass against the bug it exists to catch. So a label
 * carrying a `hidden` utility is dropped here, breakpoint variants included:
 * `hidden sm:inline` is invisible at the width a 400px pane actually is.
 */
function visibleActionLabels(): string[] {
  return [...host.querySelectorAll("button")]
    .map((button) =>
      [...button.querySelectorAll("span")]
        .filter((span) => !span.className.split(/\s+/).includes("hidden"))
        .map((span) => span.textContent?.trim() ?? "")
        .join(" ")
        .trim(),
    )
    .filter(Boolean);
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("on a page", () => {
  it("names every action", async () => {
    await render(false);
    const labels = visibleActionLabels();
    expect(labels).toContain("Playbook");
    expect(labels).toContain("View in fleet");
    expect(labels).toContain("Routines");
    expect(labels).toContain("Delete");
  });
});

describe("in the chat's pane", () => {
  it("still names every action", async () => {
    // The regression this file guards: these were icons alone here.
    await render(true);
    const labels = visibleActionLabels();
    expect(labels).toContain("Playbook");
    expect(labels).toContain("Routines");
    expect(labels).toContain("Delete");
  });

  it("shortens only the one label a narrow column cannot take whole", async () => {
    await render(true);
    const labels = visibleActionLabels();
    // Shortened, not deleted — the reader still gets a word.
    expect(labels).toContain("Fleet");
    expect(labels).not.toContain("View in fleet");
  });

  it("leaves no action reachable only by hovering it", async () => {
    await render(true);
    const headerActions = [...host.querySelectorAll("button")].filter((b) =>
      b.querySelector("svg"),
    );
    // Guard the guard: an unrendered header would satisfy the loop below by
    // having nothing in it.
    expect(headerActions.length).toBeGreaterThan(3);
    // Every icon button either carries visible text or an explicit aria-label.
    // A bare glyph with only a `title` is what this whole change removed.
    for (const button of headerActions) {
      const hasText = (button.textContent ?? "").trim().length > 0;
      const hasLabel = !!button.getAttribute("aria-label");
      expect(hasText || hasLabel).toBe(true);
    }
  });
});
