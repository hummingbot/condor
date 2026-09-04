/**
 * The Playbook band answers its own hint, and nothing the screen already said.
 *
 * The band is the one place `strategy.md` and the learnings can be read, and
 * for as long as it hosted the whole workbench it was the one place they could
 * *not*: the file was behind a button in a modal, over five bands that each
 * restated something within two inches of themselves. Four ways it could go
 * back:
 *
 * 1. Printing the documents' contents nowhere, or only behind a click.
 * 2. Restating the screen's own answers — the strategy's name, its Pause/Stop,
 *    the loop's cadence, the deployment ledger, the performance strip.
 * 3. Printing a disabled limit as the number the engine spells it with. `-1`
 *    is *no limit*, and an operator reading "max drawdown −1%" has been told
 *    something false about their own risk.
 * 4. Saying "empty" where it could say what the file is *for* — a strategy
 *    with no playbook yet reads as a broken page rather than an unwritten one.
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

import type { StrategyDetail } from "@/lib/api";

/** jsdom has none, and the editor dialogs' module graph reads the theme. */
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
    deleteStrategy: vi.fn(),
    getRoutineInstances: vi.fn(() => Promise.resolve([])),
    setRestartOnBoot: vi.fn(),
    updateStrategyMd: vi.fn(),
    updateStrategyLearnings: vi.fn(),
  },
}));

vi.mock("@/components/routines/ReportBrowser", () => ({
  ReportBrowser: () => <div data-report-browser />,
}));

const { PlaybookView } = await import("./PlaybookView");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

const STRATEGY = {
  slug: "pmm_king",
  agent_slug: "brigado",
  name: "PMM King BTC-BRL Fleet Operator",
  description: "Hourly autonomous operator for the PMM King BTC-BRL fleet.",
  strategy_md: "# Brief\n\nQuote both sides, never cross the book.",
  learnings: "## Execution notes\n\nUse raw manage_bots for fleet health.",
  config: {
    execution_mode: "loop",
    frequency_sec: 3600,
    tick_timeout_sec: 600,
    max_ticks: 0,
    restart_on_boot: true,
    server_name: "brigado_2",
    bot_mode: "auto",
    bot_name: "",
    agent_key: "",
    total_amount_quote: 100,
    risk_limits: {
      max_position_size_quote: 500,
      max_open_executors: 5,
      max_drawdown_pct: -1,
      max_leverage: -1,
    },
  },
  default_trading_context: "",
  status: "running",
  agent_id: "brigado.pmm_king_2",
  sessions: [{ number: 1 }, { number: 2 }],
  experiments: [{ number: 1 }],
  instances: [],
} as unknown as StrategyDetail;

function render(strategy: StrategyDetail = STRATEGY) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <PlaybookView
            slug="brigado"
            sslug="pmm_king"
            strategy={strategy}
            onDeleted={() => {}}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
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

describe("PlaybookView", () => {
  it("puts both documents on screen, with nothing to click first", () => {
    render();
    const text = container.textContent ?? "";
    expect(text).toContain("Quote both sides, never cross the book.");
    expect(text).toContain("Use raw manage_bots for fleet health.");
  });

  it("does not restate what the screen above it already answered", () => {
    render();
    const text = container.textContent ?? "";
    // The loop bar names the strategy and the header carries the controls.
    expect(text).not.toContain("PMM King BTC-BRL Fleet Operator");
    expect(text).not.toContain("Pause");
    // The loop bar owns the cadence countdown; the answer stack owns the money.
    expect(text).not.toContain("next in");
    expect(text).not.toContain("Total PnL");
  });

  it("draws a disabled limit as off rather than as its sentinel", () => {
    render();
    const text = container.textContent ?? "";
    expect(text).toContain("no limit");
    expect(text).not.toContain("-1%");
    expect(text).not.toContain("−1");
  });

  it("says a cadence in time and a switched-off timeout in words", () => {
    render();
    const text = container.textContent ?? "";
    expect(text).toContain("1h 00m"); // frequency_sec: 3600
    expect(text).toContain("10m 00s"); // tick_timeout_sec: 600
    expect(text).toContain("unlimited"); // max_ticks: 0
  });

  it("tells an empty playbook what it is for", () => {
    render({ ...STRATEGY, strategy_md: "" });
    expect(container.textContent).toContain("standing brief");
  });

  it("will not delete a running strategy", () => {
    render();
    const del = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Delete"),
    );
    expect(del?.disabled).toBe(true);
  });

  it("keeps the restart switch reachable, and says which way it is set", () => {
    render();
    const sw = container.querySelector('[role="switch"]');
    expect(sw?.getAttribute("aria-checked")).toBe("true");
    expect(sw?.textContent).toContain("resumes on restart");
  });
});
