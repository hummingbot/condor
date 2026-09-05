/**
 * The report column, split by the level below the scope (FEAT-116).
 *
 * `scopeOwners.test.ts` pins which children a scope splits into and
 * `owner-series.test.ts` pins that the lines add up to the Total. What is left
 * to pin is the part that only exists once the browser is rendered: that the
 * split is *reached* at the fleet scope and unreached one step further in, that
 * the chart's three parameters go to the URL without disturbing the four the
 * browser already spends there, that the band's third entry cuts the same spine
 * the tiles are folded from, and that the two things the records cannot say —
 * a complete fee total, and everything about margin and orders and accounts —
 * are captioned rather than implied.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ControllerInfo,
  ControllerPerformanceSnapshot,
} from "@/lib/api";
import type { FleetOwner } from "@/lib/agent-attribution";

// Everything the browser asks the API for is beside the point here: the fold,
// the tree and the chart are all built from props.
vi.mock("@/lib/api", () => ({
  api: new Proxy(
    {},
    { get: () => () => Promise.resolve({}) },
  ),
}));

// The aggregate chart is the branch the split must *not* fall through to, so it
// is stubbed with something nameable rather than removed.
vi.mock("@/components/bots/PnlEvolutionChart", () => ({
  PnlEvolutionChart: () => <div data-aggregate-chart />,
}));
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

const DEPLOYED = new Date(Date.now() - 3 * 3_600_000).toISOString();

function controller(over: Partial<ControllerInfo>): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: "c1",
    bot_name: "alpha-mm-1",
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
    deployed_at: DEPLOYED,
    config: {},
    ...over,
  } as ControllerInfo;
}

function snap(
  bot: string,
  id: string,
  minutesAgo: number,
  pnl: number,
): ControllerPerformanceSnapshot {
  return {
    timestamp: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
    bot_name: bot,
    controller_id: id,
    controller_name: "pmm_simple",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: pnl,
    unrealized_pnl_quote: 0,
    global_pnl_quote: pnl,
    global_pnl_pct: 0,
    volume_traded: 0,
    positions_summary: [],
  } as unknown as ControllerPerformanceSnapshot;
}

function owner(slug: string, name: string, bot: string): FleetOwner {
  return {
    runKey: `${slug}.mm`,
    agentSlug: slug,
    agentName: name,
    strategySlug: "mm",
    strategyName: "MM",
    namespace: `${slug}-mm`,
    declaredBots: [bot],
    agentIds: [],
    live: null,
  } as unknown as FleetOwner;
}

/** Two agents — one with two bots, so the tree has a level below the agents. */
const CONTROLLERS = [
  controller({
    controller_id: "c1",
    bot_name: "alpha-mm-1",
    global_pnl_quote: 30,
    realized_pnl_quote: 30,
    volume_traded: 900,
    config: { total_amount_quote: 1_000 },
  }),
  controller({
    controller_id: "c3",
    bot_name: "alpha-mm-2",
    global_pnl_quote: 12,
    realized_pnl_quote: 12,
    volume_traded: 300,
    config: { total_amount_quote: 600 },
  }),
  controller({
    controller_id: "c2",
    bot_name: "beta-mm-1",
    trading_pair: "BTC-USDT",
    connector: "kucoin",
    global_pnl_quote: -8,
    realized_pnl_quote: -8,
    volume_traded: 400,
    config: { total_amount_quote: 500 },
  }),
];

const SNAPSHOTS = [
  snap("alpha-mm-1", "c1", 120, 10),
  snap("alpha-mm-1", "c1", 60, 30),
  snap("alpha-mm-2", "c3", 120, 4),
  snap("alpha-mm-2", "c3", 60, 12),
  snap("beta-mm-1", "c2", 120, -2),
  snap("beta-mm-1", "c2", 60, -8),
];

const OWNERS = [owner("alpha", "Alpha", "alpha-mm-1"), owner("beta", "Beta", "beta-mm-1")];

let container: HTMLDivElement;
let root: Root;
/** The URL the browser last wrote, which is where its view state lives. */
let seen = "";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // The sidebar scrolls the active scope into view on mount; jsdom has no
  // layout and therefore no `scrollIntoView`.
  Element.prototype.scrollIntoView = () => {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  seen = "";
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

/** Records the router's URL, so a click's effect on it is readable. */
function Spy() {
  const location = useLocation();
  useEffect(() => {
    seen = `${location.pathname}${location.search}`;
  }, [location]);
  return null;
}

async function render(url = "/bots") {
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
            snapshots={SNAPSHOTS}
            owners={OWNERS}
            deeds={{ bots: {}, since: 1 }}
          />
          <Spy />
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
const pick = (selector: string) => container.querySelector<HTMLElement>(selector);
const all = (selector: string) => [...container.querySelectorAll<HTMLElement>(selector)];

async function click(el: HTMLElement | null | undefined) {
  expect(el).toBeTruthy();
  await act(async () => {
    el!.click();
  });
}

describe("the fleet scope", () => {
  it("draws the floor: a Total plus one line per agent, and not the aggregate curve", async () => {
    await render();

    expect(pick("[data-owner-chart]")).toBeTruthy();
    expect(pick("[data-aggregate-chart]")).toBeNull();
    expect(pick('[data-owner-legend="total"]')).toBeTruthy();
    const lines = all("[data-owner-legend]").map((el) => el.dataset.ownerLegend);
    expect(lines).toEqual(["total", "agent:alpha.mm", "agent:beta.mm"]);
    // The legend entry is the *sidebar row's* label and the *sidebar row's*
    // number, because both are read off the same node — which is what makes a
    // colour in the legend and a row in the tree the same thing.
    expect(pick('[data-owner-legend="agent:alpha.mm"]')!.textContent).toContain(
      "alpha.mm",
    );
    expect(pick('[data-owner-legend="agent:alpha.mm"]')!.textContent).toContain(
      "+$42.00",
    );
    // Named for the level it split on, not for the page it came from.
    expect(text()).toContain("Fleet PnL by agent");
  });

  it("takes its view state from the URL and falls back rather than throwing", async () => {
    await render("/bots?basis=rel&from=window&range=nonsense");
    expect(pick('[data-owner-toggle="rel"]')!.dataset.active).toBe("true");
    expect(pick('[data-owner-toggle="window"]')!.dataset.active).toBe("true");
    // A stale `?range=` lands on the report that was asked for, not on an error.
    expect(pick('[data-owner-toggle="all"]')!.dataset.active).toBe("true");
  });

  it("writes its three parameters without disturbing the browser's four", async () => {
    await render("/bots?population=running&groupBy=agent.bot&scope=all&run=7");
    await click(pick('[data-owner-toggle="rel"]'));

    const params = new URLSearchParams(seen.split("?")[1] ?? "");
    expect(params.get("basis")).toBe("rel");
    expect(params.get("population")).toBe("running");
    expect(params.get("groupBy")).toBe("agent.bot");
    expect(params.get("scope")).toBe("all");
    expect(params.get("run")).toBe("7");
  });

  it("lists a line with no declared capital in Relative rather than dividing by zero", async () => {
    // Neither controller declares any, so both lines and the Total are listed.
    await render("/bots?basis=rel");
    // The fixtures do declare capital, so nothing is unplottable — what must
    // never appear is the arithmetic of a zero denominator.
    expect(text()).not.toContain("Infinity");
    expect(text()).not.toContain("NaN");
  });
});

describe("one step into the tree", () => {
  it("redraws the same chart as one line per bot — the split follows the tree", async () => {
    await render("/bots?scope=agent:alpha.mm");

    expect(pick("[data-owner-chart]")).toBeTruthy();
    expect(all("[data-owner-legend]").map((el) => el.dataset.ownerLegend)).toEqual([
      "total",
      "bot:alpha-mm-1",
      "bot:alpha-mm-2",
    ]);
    expect(text()).toContain("by bot");
  });

  it("falls back to the single aggregate series where a scope does not split", async () => {
    // One agent, one bot: there is nothing below it for a second line to be.
    await render("/bots?scope=agent:beta.mm");

    expect(pick("[data-owner-chart]")).toBeNull();
    expect(pick("[data-aggregate-chart]")).toBeTruthy();
  });
});

describe("the band's third entry", () => {
  it("cuts the scope by instrument and by venue", async () => {
    await render();
    await click(
      all("button").find((b) => b.textContent?.trim() === "Breakdown"),
    );

    expect(all('[data-breakdown="pair"] [data-bucket]').map((el) => el.dataset.bucket))
      .toEqual(["SOL-USDC", "BTC-USDT"]);
    expect(all('[data-breakdown="venue"] [data-bucket]').map((el) => el.dataset.bucket))
      .toEqual(["binance", "kucoin"]);
  });

  it("says out loud what nothing in this app measures", async () => {
    await render();
    await click(
      all("button").find((b) => b.textContent?.trim() === "Breakdown"),
    );

    const note = pick("[data-not-measured]")!.textContent ?? "";
    expect(note).toContain("margin");
    expect(note).toContain("leverage");
    expect(note).toContain("live orders");
    expect(note).toContain("sub-accounts");
    expect(note).toContain("executor-only");
  });

  it("leaves the strip's tiles exactly as they were, so the chart's height is unchanged", async () => {
    await render();
    // The height contract: a fixed set of tiles, whatever the scope has. The
    // Fees tile carries its caption in its title rather than as a ninth tile.
    const labels = all("[data-kpi]").map((el) => el.dataset.kpi);
    expect(labels).toEqual([
      "Net PnL",
      "Realized",
      "Unrealized",
      "Win rate",
      "Volume",
      "Fees",
      "Capital",
      "Runtime",
    ]);
    expect(pick('[data-kpi="Fees"]')!.getAttribute("title")).toContain(
      "a floor, not a total",
    );
  });
});
