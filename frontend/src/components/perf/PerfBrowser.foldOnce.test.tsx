/**
 * One fold per line, and not one more (PERF-338).
 *
 * `PerfBrowser` builds two chart candidates side by side: `ownerChart`, which
 * is `aggregatePnlSeries` once per owner plus once over the union of their
 * spine keys, and `controllerPoints`, which is `aggregatePnlSeries` over that
 * same union again. The render only ever draws one of them — the JSX tests
 * `ownerChart` first — so at every scope that splits, the second fold of the
 * whole performance history was computed and thrown away, on every WS frame
 * that mints a new `snapshots` array.
 *
 * `PerfBrowser.owners.test.tsx` already pins *which* chart is drawn. What this
 * file pins is the work behind it: the count of folds per recompute, and that
 * `resolvePerfSeries` — whose last resort walks every closed outcome — is not
 * consulted at all for a chart that is not on screen.
 *
 * Nothing here is about the numbers: `aggregatePnlSeries` is called with the
 * same arguments as before and its output is untouched, so the drawn figures
 * are the ones `owners.test.tsx` already asserts.
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

import type {
  ControllerInfo,
  ControllerPerformanceSnapshot,
} from "@/lib/api";
import type { FleetOwner } from "@/lib/agent-attribution";

/**
 * The counters, hoisted so the `vi.mock` factories below — which run before
 * any module-level `const` in this file — can reach them.
 */
const spy = vi.hoisted(() => ({
  /** One entry per `aggregatePnlSeries` call: its key set, joined. */
  folds: [] as string[],
  /** How many times the chart's source was resolved. */
  resolves: 0,
}));

// Counting wrappers, not stubs: the real fold still runs, so the chart the
// assertions below read is the real one.
vi.mock("@/lib/pnl-chart", async () => {
  const actual = await vi.importActual<typeof import("@/lib/pnl-chart")>("@/lib/pnl-chart");
  return {
    ...actual,
    aggregatePnlSeries: ((...args) => {
      spy.folds.push([...args[1]].sort().join("+"));
      return actual.aggregatePnlSeries(...args);
    }) as typeof actual.aggregatePnlSeries,
  };
});

vi.mock("@/lib/perf-history", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/perf-history")>("@/lib/perf-history");
  return {
    ...actual,
    resolvePerfSeries: ((...args) => {
      spy.resolves += 1;
      return actual.resolvePerfSeries(...args);
    }) as typeof actual.resolvePerfSeries,
  };
});

// Everything the browser asks the API for is beside the point here.
vi.mock("@/lib/api", () => ({
  api: new Proxy({}, { get: () => () => Promise.resolve({}) }),
}));

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

const CONTROLLERS = [
  controller({ controller_id: "c1", bot_name: "alpha-mm-1", global_pnl_quote: 30 }),
  controller({ controller_id: "c3", bot_name: "alpha-mm-2", global_pnl_quote: 12 }),
  controller({
    controller_id: "c2",
    bot_name: "beta-mm-1",
    trading_pair: "BTC-USDT",
    connector: "kucoin",
    global_pnl_quote: -8,
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

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  spy.folds = [];
  spy.resolves = 0;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const qc = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

async function draw(url: string, snapshots: ControllerPerformanceSnapshot[]) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc()}>
        <MemoryRouter initialEntries={[url]}>
          <PerfBrowser
            controllers={CONTROLLERS}
            bots={[]}
            server="s1"
            convert={(value: number) => ({ value, converted: true })}
            currencySymbol="$"
            snapshots={snapshots}
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

/**
 * A WS frame: the same history in a new array, which is what
 * `useFleetData`'s `activeSnapshots` mints whenever the bots socket speaks.
 * Everything the memos below depend on is re-read; nothing the reader sees
 * changes.
 */
async function newFrame(url: string) {
  await draw(url, SNAPSHOTS);
  spy.folds = [];
  spy.resolves = 0;
  await draw(url, [...SNAPSHOTS]);
}

const pick = (selector: string) => container.querySelector<HTMLElement>(selector);

describe("a scope that splits", () => {
  it("folds the history once per line and once for the Total, and not a third time over the union", async () => {
    await newFrame("/bots");

    // The chart on screen is still the split one.
    expect(pick("[data-owner-chart]")).toBeTruthy();
    expect(pick("[data-aggregate-chart]")).toBeNull();

    // One fold per owner, plus the Total. Before PERF-338 there were four: the
    // union was folded twice, once for the Total and once for a candidate the
    // render never reaches.
    expect(spy.folds.length).toBe(OWNERS.length + 1);

    // And the union — the widest and most expensive of them — exactly once.
    const union = spy.folds[0];
    expect(union.split("+").length).toBe(CONTROLLERS.length);
    expect(spy.folds.filter((keys) => keys === union)).toEqual([union]);
  });

  it("does not resolve a chart source for a chart it does not draw", async () => {
    await newFrame("/bots");

    // `resolvePerfSeries`' last resort walks every closed outcome in scope.
    // Nothing downstream of it is rendered here, so it is not asked.
    expect(spy.resolves).toBe(0);
  });
});

describe("a scope that does not split", () => {
  it("still folds once and still resolves its own aggregate series", async () => {
    // One agent, one bot: there is nothing below it for a second line to be,
    // so the single aggregate curve is the chart — exactly as before.
    await newFrame("/bots?scope=agent:beta.mm");

    expect(pick("[data-owner-chart]")).toBeNull();
    expect(pick("[data-aggregate-chart]")).toBeTruthy();
    expect(spy.folds.length).toBe(1);
    expect(spy.resolves).toBeGreaterThan(0);
  });
});
