/**
 * A shut band folds nothing (PERF-340).
 *
 * The Breakdown band cuts the scope's spine two ways — by instrument and by
 * venue — and each cut is a full extra pass over that spine: `groupSpine`
 * buckets every leaf and then runs `readSpine` per bucket. Their only consumer
 * is `<ScopeBreakdowns>`, which renders strictly under `band === "breakdown"`,
 * and the band is shut on arrival every time and deliberately not remembered.
 * So for the whole of a typical session both cuts were folded and thrown away,
 * once per WS-driven `scope.leaves` change and once per 60s clock tick.
 *
 * What this file pins is the work, not the numbers: `groupSpine` is wrapped in
 * a counter, not replaced, so the tables the second half asserts are the real
 * ones, folded by the real fold with the real converter. `Σ buckets == the
 * scope's own fold` is `floor.test.ts`' business and is untouched here.
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

/** Hoisted so the `vi.mock` factory below — which runs first — can reach it. */
const spy = vi.hoisted(() => ({ /** One entry per `groupSpine` call. */ cuts: 0 }));

// A counting wrapper, not a stub: the real bucketing still runs, so the tables
// read at the foot of this file are the ones the reader would see.
vi.mock("@/components/agent/floor/floor", async () => {
  const actual =
    await vi.importActual<typeof import("@/components/agent/floor/floor")>(
      "@/components/agent/floor/floor",
    );
  return {
    ...actual,
    groupSpine: ((...args) => {
      spy.cuts += 1;
      return actual.groupSpine(...args);
    }) as typeof actual.groupSpine,
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

const DEPLOYED = new Date(Date.now() - 3 * 3_600_000).toISOString();

function position(amount: number, price: number, side: string) {
  return { amount, breakeven_price: price, side };
}

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

// Two instruments over two venues, with exposures far enough apart that the
// ranking below is a statement about the fold and not about a tie-break:
// SOL-USDC/binance is +2200 net long, BTC-USDT/kucoin is -5000 net short.
const CONTROLLERS = [
  controller({
    controller_id: "c1",
    global_pnl_quote: 30,
    positions_summary: [position(10, 200, "LONG")],
  }),
  controller({
    controller_id: "c3",
    bot_name: "alpha-mm-2",
    global_pnl_quote: 12,
    positions_summary: [position(1, 200, "LONG")],
  }),
  controller({
    controller_id: "c2",
    bot_name: "beta-mm-1",
    trading_pair: "BTC-USDT",
    connector: "kucoin",
    global_pnl_quote: -8,
    positions_summary: [position(0.1, 50_000, "SHORT")],
  }),
];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  spy.cuts = 0;
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const qc = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

async function settle() {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function draw(controllers: ControllerInfo[]) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc()}>
        <MemoryRouter initialEntries={["/bots"]}>
          <PerfBrowser
            controllers={controllers}
            bots={[]}
            server="s1"
            convert={(value: number) => ({ value, converted: true })}
            currencySymbol="$"
            snapshots={[]}
            owners={[]}
            deeds={{ bots: {}, since: 1 }}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await settle();
}

const pick = (selector: string) => container.querySelector<HTMLElement>(selector);

const buckets = (which: string) =>
  [...container.querySelectorAll(`[data-breakdown="${which}"] [data-bucket]`)].map((li) =>
    li.getAttribute("data-bucket"),
  );

async function click(selector: string) {
  const el = pick(selector);
  expect(el).toBeTruthy();
  await act(async () => {
    el!.click();
  });
  await settle();
}

describe("the Breakdown band while it is shut", () => {
  it("does not cut the spine at all", async () => {
    await draw(CONTROLLERS);

    // The tab is offered — there is a spine to cut — but nothing has cut it.
    expect(pick("[data-breakdown-toggle]")).toBeTruthy();
    expect(pick("[data-breakdown='pair']")).toBeNull();
    expect(spy.cuts).toBe(0);
  });

  it("does not cut it on a WS frame either", async () => {
    await draw(CONTROLLERS);
    spy.cuts = 0;

    // A new `controllers` array carrying the same fleet, which is what the
    // bots socket mints on every frame: every dep of the two memos is re-read.
    await draw([...CONTROLLERS]);

    expect(spy.cuts).toBe(0);
  });
});

describe("the Breakdown band once it is opened", () => {
  it("cuts the spine twice and ranks both tables by absolute exposure", async () => {
    await draw(CONTROLLERS);
    await click("[data-breakdown-toggle]");

    // One cut per table, and not one more.
    expect(spy.cuts).toBe(2);

    // The short book is the bigger position, so it leads both tables.
    expect(buckets("pair")).toEqual(["BTC-USDT", "SOL-USDC"]);
    expect(buckets("venue")).toEqual(["kucoin", "binance"]);
    expect(pick("[data-not-measured]")).toBeTruthy();
  });

  it("stops cutting when the band is shut again, leaving the other tabs whole", async () => {
    await draw(CONTROLLERS);
    await click("[data-breakdown-toggle]");
    spy.cuts = 0;

    // `toggleBand` on the open band closes it.
    await click("[data-breakdown-toggle]");
    expect(pick("[data-breakdown='pair']")).toBeNull();

    // And a frame arriving over the shut band folds nothing.
    await draw([...CONTROLLERS]);
    expect(spy.cuts).toBe(0);

    // The band's other occupants are still offered and still open.
    await click("[data-positions-toggle]");
    expect(pick("[data-breakdown='pair']")).toBeNull();
    expect(spy.cuts).toBe(0);
  });
});
