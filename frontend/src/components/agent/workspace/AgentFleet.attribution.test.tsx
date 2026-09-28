/**
 * The workspace fleet's "Showing N of M" count builds one attribution index per
 * fold, not one per controller (PERF-409).
 *
 * `PerfBrowser` is stubbed out: it takes its own index (PERF-331), and counting
 * its calls here would blur which fold paid for what. `useFleetData` hands back
 * one stable fixture so the `counts` memo evaluates exactly once.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import { attributionIndex, attributionOf, type FleetOwner } from "@/lib/agent-attribution";
import type { ControllerInfo } from "@/lib/api";

// Both entry points are wrapped: `attributionOf` reaches `attributionIndex`
// through the module's local binding, so counting the index export alone would
// pass with the per-record calls still in place.
vi.mock("@/lib/agent-attribution", async (orig) => {
  const m = await orig<typeof import("@/lib/agent-attribution")>();
  return {
    ...m,
    attributionIndex: vi.fn(m.attributionIndex),
    attributionOf: vi.fn(m.attributionOf),
  };
});

vi.mock("@/components/perf/PerfBrowser", () => ({ PerfBrowser: () => null }));

function controllerOf(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: "pmm-1",
    bot_name: "brigado-brl_mm-btc",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    global_pnl_pct: 0,
    volume_traded: 0,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: null,
    config: {},
    ...over,
  };
}

function ownerOf(slug: string, sslug: string): FleetOwner {
  return {
    runKey: `${slug}.${sslug}`,
    agentSlug: slug,
    agentName: slug,
    strategySlug: sslug,
    strategyName: sslug,
    namespace: `${slug}-${sslug}`,
    declaredBots: [],
    agentIds: [],
    live: null,
  };
}

const FLEET = {
  controllers: [
    controllerOf({ controller_id: "a" }),
    controllerOf({ controller_id: "b" }),
    controllerOf({ controller_id: "c", bot_name: "vega-momentum-eth" }),
    controllerOf({ controller_id: "d", bot_name: "vega-momentum-sol" }),
    controllerOf({ controller_id: "e", bot_name: "stranger" }),
  ],
  owners: [ownerOf("brigado", "brl_mm"), ownerOf("vega", "momentum")],
  deeds: null,
  bots: [],
  executors: [],
  paging: {},
  snapshots: [],
  truncated: false,
  runs: [],
  terminatedControllers: [],
  convert: (value: number) => ({ value, converted: true }),
  currencySymbol: "$",
  rateFormatPnl: (v: number) => `$${v}`,
  rateFormatValue: (v: number) => `$${v}`,
  rateFormatDetailed: (v: number) => `$${v}`,
  isLoading: false,
  error: null,
  serverOnline: true,
};

vi.mock("@/hooks/useFleetData", () => ({ useFleetData: () => FLEET }));

const { AgentFleet } = await import("./AgentFleet");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("AgentFleet counts (PERF-409)", () => {
  it("takes the attribution index once for the whole fold", async () => {
    vi.mocked(attributionIndex).mockClear();
    vi.mocked(attributionOf).mockClear();
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/agents/brigado?view=fleet"]}>
          <ServerContext.Provider value={{ server: "srv", setServer: () => {} }}>
            <AgentFleet slug="brigado" sslug="brl_mm" serverName="" run={null} />
          </ServerContext.Provider>
        </MemoryRouter>,
      );
    });

    // Same answer as the per-record rule: two of the five are Brigado's.
    expect(container.textContent).toContain("Showing 2 of 5 controllers");
    expect(vi.mocked(attributionOf)).not.toHaveBeenCalled();
    expect(vi.mocked(attributionIndex)).toHaveBeenCalledTimes(1);
  });
});
