/**
 * The portfolio header says when it has no answer (FP-10).
 *
 * Every tile in the strip read only its query's `data`, so a failed request
 * folded to `0`, `+$0.00` and "No history data available": an API outage
 * reading as an idle account with nothing in it. A tile whose query failed
 * now reads "—" and says what could not be loaded; the history chart says the
 * load failed and offers to ask again.
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

import { ServerContext } from "@/hooks/useServer";

const calls = vi.hoisted(() => ({
  getPortfolio: vi.fn(),
  getBots: vi.fn(),
  getExecutors: vi.fn(),
  getExecutorsSummary: vi.fn(),
  getPortfolioHistory: vi.fn(),
  getConsolidatedPositions: vi.fn(),
  getAgents: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: calls }));

vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => {} }));
vi.mock("@/hooks/useLpPositions", () => ({
  useLpPositions: () => ({ positions: [], label: "", isLoading: false }),
}));
vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    convert: (value: number) => ({ value, converted: true }),
    formatValueDetailed: (v: number) => String(v),
    formatPnlValue: (v: number) => String(v),
    resolvedSymbol: "$",
  }),
}));
vi.mock("@/components/portfolio/PositionsTab", () => ({ PositionsTab: () => null }));

const { Portfolio } = await import("./Portfolio");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

async function render() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider value={{ server: "s1", setServer: () => {} }}>
          <MemoryRouter>
            <Portfolio />
          </MemoryRouter>
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** A strip tile's whole text, found by its label. */
function tile(label: string): string {
  const el = [...container.querySelectorAll("span")].find((s) => s.textContent === label);
  if (!el?.parentElement) throw new Error(`no ${label} tile`);
  return el.parentElement.textContent ?? "";
}

const history = (...totals: number[]) => ({
  points: totals.map((total_usd, i) => ({ timestamp: 1_700_000_000 + i * 3600, total_usd })),
  top_tokens: [],
});

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  for (const fn of Object.values(calls)) fn.mockReset();
  calls.getPortfolio.mockResolvedValue({ total_usd: 120, connectors: [] });
  calls.getBots.mockResolvedValue({ bots: [{ bot_name: "b1" }], controllers: [] });
  calls.getExecutors.mockResolvedValue([]);
  calls.getExecutorsSummary.mockResolvedValue({ count: 3, pnl: 5, volume: 900, converted: true });
  calls.getPortfolioHistory.mockResolvedValue(history(100, 110));
  calls.getConsolidatedPositions.mockResolvedValue({ executor_positions: [], bot_positions: [] });
  calls.getAgents.mockResolvedValue([]);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("Portfolio strip, when a tile's request fails", () => {
  it("reads normally when everything answers", async () => {
    await render();

    expect(tile("Bots")).toContain("1");
    expect(tile("Bots")).toContain("0 controllers");
    expect(tile("Executors")).toContain("0 active");
    expect(tile("Executors")).toContain("3 in 1W");
    expect(tile("Agents")).toContain("0 total");
  });

  it("does not report zero bots when the bots request failed", async () => {
    calls.getBots.mockRejectedValue(new Error("down"));
    await render();

    expect(tile("Bots")).toContain("—");
    expect(tile("Bots")).toContain("Could not load bots");
    expect(tile("Bots")).not.toContain("0 controllers");
  });

  // The bots route answers an unreachable trading server in band — a 200 with
  // `server_online: false` and empty lists — so the query resolves and only
  // the flag says the server never answered.
  it("does not report zero bots when the server answered offline", async () => {
    calls.getBots.mockResolvedValue({
      bots: [],
      controllers: [],
      total_pnl: 0,
      total_volume: 0,
      server_online: false,
      error_hint: "Connection error",
    });
    await render();

    expect(tile("Bots")).toContain("—");
    expect(tile("Bots")).toContain("Could not load bots");
    expect(tile("Bots")).not.toContain("0 controllers");
  });

  it("says the period summary failed instead of loading for ever", async () => {
    calls.getExecutorsSummary.mockRejectedValue(new Error("down"));
    await render();

    expect(tile("Executors")).toContain("Could not load 1W");
    expect(tile("Executors")).not.toContain("Loading");
  });

  it("does not report zero active executors or zero agents on a failure", async () => {
    calls.getExecutors.mockRejectedValue(new Error("down"));
    calls.getAgents.mockRejectedValue(new Error("down"));
    await render();

    expect(tile("Executors")).not.toContain("0 active");
    expect(tile("Agents")).toContain("Could not load agents");
  });
});

describe("Portfolio history chart", () => {
  it("says the load failed, with Retry, instead of 'No history data available'", async () => {
    calls.getPortfolioHistory.mockRejectedValue(new Error("down"));
    await render();

    const state = container.querySelector<HTMLElement>('[data-chart-state="error"]');
    expect(state?.textContent).toContain("Could not load portfolio history");
    expect(container.textContent).not.toContain("No history data available");

    calls.getPortfolioHistory.mockResolvedValue(history(100, 110));
    const before = calls.getPortfolioHistory.mock.calls.length;
    await act(async () => {
      state!.querySelector("button")!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(calls.getPortfolioHistory.mock.calls.length).toBeGreaterThan(before);
  });

  it("says the load failed when the server answered offline, not that there is no history", async () => {
    calls.getPortfolioHistory.mockResolvedValue({
      ...history(),
      server_online: false,
      error_hint: "Unable to reach server",
    });
    await render();

    const state = container.querySelector<HTMLElement>('[data-chart-state="error"]');
    expect(state?.textContent).toContain("Could not load portfolio history");
    expect(container.textContent).not.toContain("No history data available");

    calls.getPortfolioHistory.mockResolvedValue(history(100, 110));
    await act(async () => {
      state!.querySelector("button")!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    for (let i = 0; i < 4; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }
    expect(container.querySelector('[data-chart-state="error"]')).toBeNull();
  });

  it("still says there is no history when the request answers with none", async () => {
    calls.getPortfolioHistory.mockResolvedValue(history());
    await render();

    expect(container.textContent).toContain("No history data available");
    expect(container.querySelector('[data-chart-state="error"]')).toBeNull();
  });
});

/**
 * The arrow beside the total is the period's last history point minus its
 * first -- both ends from the one series.
 *
 * FP-15 made it "live total − first point". The live total is deduped for
 * unified accounts and the history is not, so the two are different quantities:
 * an account double-counted in history showed a permanent false loss.
 */
describe("Portfolio period change", () => {
  it("measures the period between the history's own two ends, not against the live total", async () => {
    // Defaults: live total 120, history [100, 110].
    await render();

    expect(tile("Portfolio")).toContain("+$10.00");
    expect(tile("Portfolio")).not.toContain("+$20.00");
    expect(container.querySelector('[title^="Change in portfolio value over 1W"]')).not.toBeNull();
  });

  it("shows no false loss when history double-counts what the live total dedupes", async () => {
    // A unified account: history carries it twice (240), the live total once (120).
    calls.getPortfolioHistory.mockResolvedValue(history(240, 240));
    await render();

    expect(tile("Portfolio")).not.toContain("-$");
    expect(tile("Portfolio")).toContain("+$0.00");
  });

  it("shows no change at all when the period has no history to measure from", async () => {
    calls.getPortfolioHistory.mockResolvedValue(history());
    await render();

    expect(tile("Portfolio")).not.toContain("+$");
    expect(tile("Portfolio")).not.toContain("-$");
  });
});
