/**
 * Stop-executor / clear-position confirmations in the trade pane (FU-01, FU-12).
 *
 * Both mutations used to close their dialog from `onSettled`, so a rejected
 * request looked exactly like a successful one while the executor kept
 * trading. These pin the contract: the dialog closes on success only, shows
 * the error on failure, and a second click while pending posts nothing.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import type { ConsolidatedPosition, ExecutorInfo } from "@/lib/api";
import { TradeBottomPane } from "./TradeBottomPane";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const { stopExecutor, clearPositionHeld } = vi.hoisted(() => ({
  stopExecutor: vi.fn(),
  clearPositionHeld: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      stopExecutor,
      clearPositionHeld,
      getPortfolio: vi.fn(async () => ({
        server: "prod",
        connectors: [],
        total_usd: 0,
      })),
      // 5 BRL per display dollar.
      getRates: vi.fn(async () => ({ rates: { "USDT-BRL": 5 } })),
    },
  };
});

const EXECUTOR = {
  id: "abcdef1234567890",
  type: "position",
  connector: "binance",
  trading_pair: "BTC-BRL",
  side: "BUY",
  status: "running",
  close_type: "",
  pnl: 0,
  volume: 0,
  timestamp: 1_700_000_000,
  controller_id: "main",
  cum_fees_quote: 0,
  net_pnl_pct: 0,
  entry_price: 0,
  current_price: 0,
  close_timestamp: 0,
  custom_info: {},
  config: {},
} as ExecutorInfo;

const POSITION = {
  connector_name: "binance",
  trading_pair: "BTC-BRL",
  position_side: "LONG",
  amount: 2,
  entry_price: 250,
  notional_value: 500,
  current_price: 250,
  unrealized_pnl: 0,
  realized_pnl: 0,
  cum_fees: 10,
  executor_count: 0,
  leverage: 1,
  controller_id: "main",
  source: "executor",
  source_name: "main",
} as ConsolidatedPosition;

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

async function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <ServerContext.Provider
            value={{ server: "prod", setServer: () => {} }}
          >
            <TradeBottomPane
              executors={[EXECUTOR]}
              positions={[POSITION]}
              isLoadingPositions={false}
              connector="binance"
              pair="BTC-BRL"
              isSpot
            />
          </ServerContext.Provider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await flush();
}

function button(label: string): HTMLButtonElement {
  const hit = [...document.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === label || b.title === label,
  );
  if (!hit) throw new Error(`no button "${label}"`);
  return hit as HTMLButtonElement;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await flush();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  stopExecutor.mockReset();
  clearPositionHeld.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("TradeBottomPane stop executor", () => {
  it("keeps the dialog open and shows the error when the stop is rejected", async () => {
    stopExecutor.mockRejectedValue(new Error("Hummingbot API unreachable"));
    await mount();
    await click(button("Stop executor"));
    await click(button("Stop"));

    expect(stopExecutor).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).toContain("Hummingbot API unreachable");
    // Still there to retry.
    expect(button("Stop")).toBeTruthy();
  });

  it("closes the dialog once the stop succeeds", async () => {
    stopExecutor.mockResolvedValue({});
    await mount();
    await click(button("Stop executor"));
    await click(button("Stop"));

    expect(stopExecutor).toHaveBeenCalledWith("prod", EXECUTOR.id, false);
    expect(() => button("Stop")).toThrow();
  });

  it("does not post twice when the confirm is clicked while pending", async () => {
    let resolve!: (v: unknown) => void;
    stopExecutor.mockReturnValue(new Promise((r) => (resolve = r)));
    await mount();
    await click(button("Stop executor"));
    await click(button("Stop"));

    const pending = button("Stopping...");
    expect(pending.disabled).toBe(true);
    await click(pending);
    expect(stopExecutor).toHaveBeenCalledTimes(1);

    await act(async () => resolve({}));
    await flush();
    expect(() => button("Stopping...")).toThrow();
  });

  it("drops the previous attempt's error when the dialog is reopened", async () => {
    stopExecutor.mockRejectedValue(new Error("boom"));
    await mount();
    await click(button("Stop executor"));
    await click(button("Stop"));
    expect(document.body.textContent).toContain("boom");

    await click(button("Cancel"));
    await click(button("Stop executor"));
    expect(document.body.textContent).not.toContain("boom");
  });
});

describe("TradeBottomPane clear position", () => {
  async function openPositions() {
    await mount();
    await click(
      [...document.querySelectorAll("button")].find((b) =>
        b.textContent?.trim().startsWith("Positions"),
      ) as HTMLButtonElement,
    );
  }

  it("keeps the dialog open and shows the error when the clear is rejected", async () => {
    clearPositionHeld.mockRejectedValue(new Error("clear failed upstream"));
    await openPositions();
    await click(button("Clear position"));
    await click(button("Clear"));

    expect(clearPositionHeld).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).toContain("clear failed upstream");
    expect(button("Clear")).toBeTruthy();
  });

  it("closes the dialog once the clear succeeds", async () => {
    clearPositionHeld.mockResolvedValue({});
    await openPositions();
    await click(button("Clear position"));
    await click(button("Clear"));

    expect(() => button("Clear")).toThrow();
  });

  it("renders quote-denominated notional and fees through the rate, not a literal $", async () => {
    await openPositions();
    // 500 BRL notional and 10 BRL fees at 5 BRL per dollar.
    expect(container.textContent).toContain("($100.00)");
    expect(container.textContent).toContain("Fees: $2.00");
    expect(container.textContent).not.toContain("$500.00");
  });
});
