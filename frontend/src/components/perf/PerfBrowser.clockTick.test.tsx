/**
 * The wall clock moves the runtimes, not the tree (PERF-339).
 *
 * `PerfBrowser` subscribes to a clock quantised to 60 s so that every "per
 * hour" figure divides by a runtime that is not stale. That clock used to be a
 * dependency of `leavesFor`, though its only use inside the callback was the
 * terminated window's cutoff — the running branch never asks the time at all.
 * So once a minute, in both populations, the callback got a new identity and
 * `rawLeaves` → `leaves` → `tree` → `nodes` → `scope` and every fold hanging
 * off them were rebuilt for an answer that is identical unless a record
 * crossed the window edge in that minute.
 *
 * What this file pins is the split: on a bare tick the leaf population and the
 * scope tree are not rebuilt, while the folds that legitimately divide by
 * elapsed time still are. And that the window itself did not stop moving —
 * quantising the cutoff to the hour must not stop `period` from re-filtering
 * what has finished.
 *
 * Counting wrappers, never stubs: the real `runningLeaves`, `buildTree` and
 * `foldLeaves` still run, so what is asserted is the work behind the screen the
 * other `PerfBrowser` tests already pin.
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

import type { ControllerInfo, ExecutorInfo } from "@/lib/api";

/** The counters, hoisted so the `vi.mock` factories below can reach them. */
const spy = vi.hoisted(() => ({
  /** How many times the live fleet's leaf population was built. */
  populations: 0,
  /** One entry per `buildTree` call: how many leaves it was handed. */
  trees: [] as number[],
  /** How many times a leaf set was folded into totals. */
  folds: 0,
}));

vi.mock("@/lib/perf-population", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/perf-population")>("@/lib/perf-population");
  return {
    ...actual,
    runningLeaves: ((...args) => {
      spy.populations += 1;
      return actual.runningLeaves(...args);
    }) as typeof actual.runningLeaves,
  };
});

vi.mock("@/lib/perf-tree", async () => {
  const actual = await vi.importActual<typeof import("@/lib/perf-tree")>("@/lib/perf-tree");
  return {
    ...actual,
    buildTree: ((...args) => {
      spy.trees.push(args[0].length);
      return actual.buildTree(...args);
    }) as typeof actual.buildTree,
    foldLeaves: ((...args) => {
      spy.folds += 1;
      return actual.foldLeaves(...args);
    }) as typeof actual.foldLeaves,
  };
});

// Everything the browser asks the API for is beside the point here.
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

/** The tick the clock is quantised to, and the window it used to drag with it. */
const CLOCK_TICK_MS = 60_000;
const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

/**
 * The instant every clock in this file starts from, and why it is pinned.
 *
 * `windowCutoff` is floored to the *hour* while the clock ticks every *minute*,
 * so a bare tick moves the cutoff — and with it `leavesFor`, `rawLeaves` and
 * the tree — on exactly one minute in sixty: the one that crosses an hour
 * boundary. Left on the ambient wall clock this file therefore asserted "a tick
 * rebuilds nothing" while standing, once an hour, on the one tick that
 * legitimately rebuilds everything, and failed for ~61 s in every hour.
 *
 * Landing on minute 00 leaves the whole hour between the start and the next
 * boundary, so the `shouldAdvanceTime` drift below — real seconds, not minutes —
 * cannot walk the test into that minute. The crossing itself is not left
 * untested: `HOUR_EDGE` pins it deliberately.
 */
const NOW = Date.parse("2026-09-04T12:00:00Z");

/** A minute before the next hour: the one tick that *must* move the window. */
const HOUR_EDGE = NOW + 59 * CLOCK_TICK_MS;

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
    deployed_at: new Date(NOW - 3 * HOUR_MS).toISOString(),
    config: {},
    ...over,
  } as ControllerInfo;
}

/** A closed executor that stopped `daysAgo` ago — the only thing the window cuts. */
function closed(id: string, daysAgo: number): ExecutorInfo {
  const endedAt = NOW - daysAgo * DAY_MS;
  return {
    id,
    type: "position_executor",
    connector: "binance",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "terminated",
    close_type: "TAKE_PROFIT",
    pnl: 1,
    volume: 10,
    timestamp: endedAt - 3_600_000,
    controller_id: "",
    cum_fees_quote: 0,
    net_pnl_pct: 0,
    entry_price: 1,
    current_price: 1,
    close_timestamp: endedAt,
    custom_info: {},
    config: {},
  };
}

const CONTROLLERS = [
  controller({ controller_id: "c1", bot_name: "alpha-mm-1", global_pnl_quote: 30 }),
  controller({
    controller_id: "c2",
    bot_name: "beta-mm-1",
    trading_pair: "BTC-USDT",
    connector: "kucoin",
    global_pnl_quote: -8,
  }),
];

/** Two inside a week, one only inside a quarter. */
const EXECUTORS = [closed("x-recent-1", 1), closed("x-recent-2", 2), closed("x-old", 45)];

// Held out here rather than written into the JSX below, and passed rather than
// left to their defaults: both real mount sites (`pages/Bots.tsx`,
// `agent/workspace/AgentFleet.tsx`) hand the browser memoised arrays from
// `useFleetData`, whereas the `= []` defaults in the signature mint a fresh one
// on every render — which would invalidate `leavesFor` by itself and hide the
// very thing this file measures.
const NO_RUNS: never[] = [];
const NO_TERMINATED: never[] = [];
const NO_SNAPSHOTS: never[] = [];
const NO_DEEDS = { bots: {}, since: 1 };

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // `shouldAdvanceTime` keeps the `setTimeout(0)` pumping below working while
  // still letting the test drive the clock's own interval by hand. `now` pins
  // where in the hour that drift starts — see `NOW`.
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW });
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  spy.populations = 0;
  spy.trees = [];
  spy.folds = 0;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

const qc = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

async function draw(url: string) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc()}>
        <MemoryRouter initialEntries={[url]}>
          <PerfBrowser
            controllers={CONTROLLERS}
            executors={EXECUTORS}
            bots={[]}
            server="s1"
            convert={(value: number) => ({ value, converted: true })}
            currencySymbol="$"
            runs={NO_RUNS}
            terminatedControllers={NO_TERMINATED}
            snapshots={NO_SNAPSHOTS}
            deeds={NO_DEEDS}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await settle();
}

async function settle() {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** One turn of the 60 s clock, and nothing else. */
async function tick() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(CLOCK_TICK_MS);
  });
  await settle();
}

const buttonNamed = (label: string) =>
  [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === label);

describe("a bare clock tick", () => {
  it("rebuilds neither the leaf population nor the scope tree", async () => {
    await draw("/bots");
    spy.populations = 0;
    spy.trees = [];

    await tick();

    // Before PERF-339 both of these ran again every minute — `leavesFor` took
    // `now`, so `rawLeaves` and everything downstream of it was reallocated for
    // an identical answer.
    expect(spy.populations).toBe(0);
    expect(spy.trees).toEqual([]);
  });

  it("still refolds the totals, so every runtime divisor moves", async () => {
    await draw("/bots");
    spy.folds = 0;

    await tick();

    // `totals` and the per-row folds keep taking the clock itself: what the
    // reader sees per hour is still a minute old at worst.
    expect(spy.folds).toBeGreaterThan(0);
  });

  it("leaves the terminated tree alone too, where the window does live", async () => {
    await draw("/bots?population=terminated");
    spy.trees = [];

    await tick();

    expect(spy.trees).toEqual([]);
  });

  // The other three tests in this describe are only worth anything if a tick
  // *can* rebuild the tree — otherwise they would still pass against a browser
  // that had stopped tracking the window at all. This is the tick that must,
  // and it is also the one this file used to land on by accident once an hour.
  it("does rebuild when the tick crosses an hour, where the cutoff moves", async () => {
    vi.setSystemTime(HOUR_EDGE);
    await draw("/bots?population=terminated");
    spy.populations = 0;
    spy.trees = [];

    await tick();

    expect(spy.trees.length).toBeGreaterThan(0);
  });
});

describe("the terminated window", () => {
  it("re-filters what has finished when the period changes", async () => {
    await draw("/bots?population=terminated");

    // The default period is 3M: all three closed executors are in.
    const atQuarter = spy.trees.at(-1);
    expect(atQuarter).toBe(EXECUTORS.length);

    spy.trees = [];
    await act(async () => {
      buttonNamed("1W")?.click();
    });
    await settle();

    // A week keeps the two that closed in it and drops the one 45 days back —
    // the hourly cutoff is coarser in time, not blunter about the period.
    expect(spy.trees.at(-1)).toBe(2);
  });
});
