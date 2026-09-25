/**
 * A run whose server could not be read (CORR-433).
 *
 * The run filter keeps only the run's own records, and an unread ledger has
 * none, so the fold is empty. Folding it into a strip of `$0.00` reads as a
 * measured result. The strip must read "—" and a notice must say why. A run
 * that really deployed nothing still reads `$0.00`, because for that run zero
 * is the true answer.
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

import type { ControllerInfo, StrategyUnavailable } from "@/lib/api";
import type { FleetOwner } from "@/lib/agent-attribution";

/** What the run ledger answers; each case sets it before rendering. */
let unavailable: StrategyUnavailable = "";

vi.mock("@/lib/api", () => ({
  api: new Proxy(
    {},
    {
      get: (_target, name) =>
        name === "getStrategySessionExecutors"
          ? () =>
              Promise.resolve({
                deployments: [],
                executors: [],
                pnl_series: [],
                unavailable,
              })
          : () => Promise.resolve({}),
    },
  ),
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

const CONTROLLERS = [
  {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: "c1",
    bot_name: "alpha-mm-1",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 30,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 30,
    global_pnl_pct: 0,
    volume_traded: 900,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    config: { total_amount_quote: 1_000 },
  } as unknown as ControllerInfo,
];

const OWNERS = [
  {
    runKey: "alpha.mm",
    agentSlug: "alpha",
    agentName: "Alpha",
    strategySlug: "mm",
    strategyName: "MM",
    namespace: "alpha-mm",
    declaredBots: ["alpha-mm-1"],
    agentIds: [],
    live: null,
  } as unknown as FleetOwner,
];

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

async function render(url: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[url]}>
          <PerfBrowser
            controllers={CONTROLLERS}
            bots={[]}
            server="s1"
            convert={(value: number) => ({ value, converted: true })}
            currencySymbol="$"
            snapshots={[]}
            owners={OWNERS}
            deeds={{ bots: {}, since: 1 }}
          />
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

const text = () => container.textContent ?? "";
const kpiValue = (label: string) =>
  container.querySelector<HTMLElement>(`[data-kpi="${label}"]`)?.children[1]?.textContent ?? null;

const RUN_URL = "/bots?scope=agent:alpha.mm&run=s3";

describe("a run filtered to nothing", () => {
  it("reads — and says the figures are withheld when its server could not be read", async () => {
    unavailable = "no_access";
    await render(RUN_URL);

    for (const label of ["Net PnL", "Realized", "Unrealized", "Volume", "Fees", "Capital", "Win rate"]) {
      expect(kpiValue(label)).toBe("—");
    }
    expect(text()).not.toContain("$0.00");
    const notice = container.querySelector<HTMLElement>("[data-run-withheld]");
    expect(notice?.textContent).toContain("Server access unavailable for this strategy");
    expect(notice?.textContent).toContain("withheld");
  });

  it("still reads $0.00 when the run really deployed nothing", async () => {
    unavailable = "";
    await render(RUN_URL);

    expect(kpiValue("Net PnL")).toContain("$0.00");
    expect(container.querySelector("[data-run-withheld]")).toBeNull();
  });
});

describe("without a run filter", () => {
  it("folds the scope's figures as before and draws no withheld notice", async () => {
    unavailable = "no_access";
    await render("/bots?scope=agent:alpha.mm");

    expect(kpiValue("Net PnL")).toContain("30.00");
    expect(container.querySelector("[data-run-withheld]")).toBeNull();
  });
});
