/**
 * A quote with no rate is never passed off as display currency (FP-01).
 *
 * `useRates().convert` hands a value back in quote units, flagged
 * `converted: false`, when it has no rate — while the rate table is loading,
 * and for good when the server has no path for the pair. The fold kept the
 * number and dropped the flag, so a BRL controller's +5,000 was added to a
 * dollar fleet as +5,000 and drawn under a `$` with nothing said.
 *
 * Two states, two answers. Rates *loading*: the money tiles read "—", because
 * the right number is a request away and the wrong one must not flash. No rate
 * *at all*: the fold stands at face value and the scope says which quote it
 * could not convert.
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

import type { ControllerInfo } from "@/lib/api";
import type { ConvertFn } from "@/lib/rates";

vi.mock("@/lib/api", () => ({
  api: new Proxy({}, { get: () => () => Promise.resolve({}) }),
}));

vi.mock("@/components/bots/PnlEvolutionChart", () => ({ PnlEvolutionChart: () => null }));
vi.mock("@/components/bots/ControllerPnlChart", () => ({ ControllerPnlChart: () => null }));
vi.mock("@/components/charts/ExecutorChart", () => ({ ExecutorChart: () => null }));
vi.mock("@/components/editor/EditorModal", () => ({ EditorModal: () => null }));
vi.mock("@/components/bots/LogsSection", () => ({ LogsSection: () => null }));
vi.mock("@/components/bots/DeployBotDialog", () => ({ DeployBotDialog: () => null }));
vi.mock("@/components/bots/ArchivedBotDetail", () => ({ ArchivedBotDetail: () => null }));
vi.mock("@/components/perf/YamlConfigEditor", () => ({ YamlConfigEditor: () => null }));
vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => {} }));

const { PerfBrowser } = await import("@/components/perf/PerfBrowser");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function controller(id: string, pair: string, pnl: number): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: id,
    bot_name: `bot-${id}`,
    status: "running",
    connector: "binance",
    trading_pair: pair,
    realized_pnl_quote: pnl,
    unrealized_pnl_quote: 0,
    global_pnl_quote: pnl,
    global_pnl_pct: 0,
    volume_traded: 0,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    config: {},
  } as unknown as ControllerInfo;
}

/** The finding's fleet: +100 in a dollar quote, +5,000 in reais. */
const MIXED = [controller("sol", "SOL-USDC", 100), controller("brl", "BTC-BRL", 5000)];
const DOLLARS_ONLY = [controller("sol", "SOL-USDC", 100)];

/** `useRates().convert` under a USD display currency, over a given BRL rate. */
const convertWith = (brl: number | null): ConvertFn => (value, quote) => {
  const rate = quote === "USDC" || quote === "USDT" ? 1 : quote === "BRL" ? brl : null;
  return rate != null ? { value: value / rate, converted: true } : { value, converted: false };
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(props: { controllers: ControllerInfo[]; convert: ConvertFn; ratesLoading?: boolean }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/bots"]}>
          <PerfBrowser bots={[]} server="s1" currencySymbol="$" snapshots={[]} {...props} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const kpiValue = (label: string) =>
  container.querySelector<HTMLElement>(`[data-kpi="${label}"]`)?.children[1]?.textContent ?? null;
const note = (kind: string) =>
  container.querySelector<HTMLElement>(`[data-unconverted="${kind}"]`)?.textContent ?? null;

describe("PerfBrowser, a fleet quoted in two currencies", () => {
  it("converts both and says nothing once the rates are in", async () => {
    await render({ controllers: MIXED, convert: convertWith(5.4) });

    expect(kpiValue("Net PnL")).toContain("1,025.93");
    expect(note("no-rate")).toBeNull();
    expect(note("loading")).toBeNull();
  });

  it("withholds the money tiles while the rates are loading", async () => {
    await render({ controllers: MIXED, convert: convertWith(null), ratesLoading: true });

    for (const label of ["Net PnL", "Realized", "Unrealized", "Volume", "Fees", "Capital"]) {
      expect(kpiValue(label)).toBe("—");
    }
    // The number the bug drew: 100 dollars plus 5,000 reais, as dollars.
    expect(container.textContent).not.toContain("5,100.00");
    expect(note("loading")).toContain("BRL");
  });

  it("names the quote it could not convert when there is no rate for it", async () => {
    await render({ controllers: MIXED, convert: convertWith(null) });

    // Face value, disclosed: dropping the controller would be a different
    // wrong number, and the line beside the scope says what this one is.
    expect(kpiValue("Net PnL")).toContain("5,100.00");
    expect(note("no-rate")).toContain("BRL");
    expect(note("no-rate")).toContain("not converted");
    // The sidebar rows that fold the reais are marked; the dollar row is not.
    const rows = [...container.querySelectorAll<HTMLElement>("span[title^='No conversion rate for BRL']")];
    expect(rows.map((r) => r.textContent)).toContain("+$5,000.00 ⚠");
    expect(container.textContent).not.toContain("+$100.00 ⚠");
  });

  it("does not withhold a fleet that needs no rate, even mid-load", async () => {
    await render({ controllers: DOLLARS_ONLY, convert: convertWith(null), ratesLoading: true });

    expect(kpiValue("Net PnL")).toContain("100.00");
    expect(note("loading")).toBeNull();
    expect(note("no-rate")).toBeNull();
  });
});
