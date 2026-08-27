/**
 * The one thing AggregatedPnlChart decides on its own: which controllers are
 * folded in (PERF-240).
 *
 * The selection is a `Set`, and that Set is an argument of the `data` memo, so
 * its *identity* is load-bearing: every `bots` WS frame hands the component a
 * brand-new `controllers` array built from the very same ids, and if the
 * selection is rebuilt along with it the whole aggregation re-runs — twice per
 * frame, since re-syncing the selection while rendering also forces React to
 * render the component again. These tests pin the identity, not the drawing.
 *
 * `aggregatePnlSeries` is wrapped rather than stubbed, so each entry in `folds`
 * is one real execution of the fold: the length of that array is how many times
 * the memo actually recomputed, and the recorded `enabledIds` is the identity
 * the memo saw. The chart shell is stubbed down to its `filters` node — the
 * chip strip — because the pixels are PnlEvolutionChart's business.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const { folds } = vi.hoisted(() => ({ folds: [] as Set<string>[] }));

vi.mock("@/lib/pnl-chart", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/pnl-chart")>();
  const aggregatePnlSeries: typeof mod.aggregatePnlSeries = (snapshots, enabledIds, controllers, convertFn) => {
    folds.push(enabledIds);
    return mod.aggregatePnlSeries(snapshots, enabledIds, controllers, convertFn);
  };
  return { ...mod, aggregatePnlSeries };
});

vi.mock("./PnlEvolutionChart", async () => {
  const { createElement } = await import("react");
  const PnlEvolutionChart = ({ filters }: { filters?: ReactNode }) =>
    createElement("div", { "data-chart": "" }, filters);
  return { PnlEvolutionChart };
});

const { AggregatedPnlChart } = await import("./AggregatedPnlChart");

/** A stored snapshot for `id` at `hhmm`. */
function snap(id: string, hhmm: string, botName = "bot"): ControllerPerformanceSnapshot {
  return {
    timestamp: `2026-08-27T${hhmm}:00Z`,
    bot_name: botName,
    controller_id: id,
    controller_name: id,
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 1,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 1,
    global_pnl_pct: 0,
    volume_traded: 10,
    positions_summary: [],
  };
}

/**
 * A live controller. Every call returns a fresh object, which is the point:
 * a WS frame never reuses the objects it decoded, only the ids inside them.
 */
function ctrl(id: string, botName = "bot"): ControllerInfo {
  return {
    controller_name: id,
    controller_id: id,
    bot_name: botName,
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 1,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 1,
    global_pnl_pct: 0,
    volume_traded: 10,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: null,
    config: {},
  };
}

const snapshots = [snap("ctrl-a", "10:00"), snap("ctrl-b", "10:00"), snap("ctrl-a", "11:00"), snap("ctrl-b", "11:00")];

let container: HTMLDivElement;
let root: Root;

/** Render one WS frame's worth of controllers, always as freshly built objects. */
function render(ids: string[]) {
  act(() => {
    root.render(<AggregatedPnlChart snapshots={snapshots} controllers={ids.map((id) => ctrl(id))} />);
  });
}

/**
 * The selection key for a controller of bot "bot" — the fold is keyed by bot
 * *and* controller id, never the id alone (CORR-241).
 */
const key = (id: string) => `bot:${id}`;

/** The labels of the chips currently on screen, in order ("All" excluded). */
function chips(): string[] {
  return [...container.querySelectorAll("button")].map((b) => b.textContent ?? "").filter((t) => t !== "All");
}

function clickChip(id: string) {
  const button = [...container.querySelectorAll("button")].find((b) => b.textContent === id);
  if (!button) throw new Error(`no chip for ${id}`);
  act(() => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

/** The selection the last fold ran against. */
const lastFold = () => folds[folds.length - 1];

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  folds.length = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/**
 * Two bots deployed from one controller config (CORR-241).
 *
 * They share a `controller_id`, so keyed on that alone the fleet collapsed to
 * one chip driving one series. Each bot is its own controller, so each gets its
 * own chip — and since the bare id would then name two different lines, the
 * label picks up the bot to tell them apart.
 */
describe("AggregatedPnlChart with two bots on one controller config", () => {
  const shared = "grid_sol";
  const controllers = [ctrl(shared, "alpha"), ctrl(shared, "beta"), ctrl("pmm", "alpha")];
  const twoBotSnapshots = [
    snap(shared, "10:00", "alpha"),
    snap(shared, "10:00", "beta"),
    snap("pmm", "10:00", "alpha"),
    snap(shared, "11:00", "alpha"),
    snap(shared, "11:00", "beta"),
    snap("pmm", "11:00", "alpha"),
  ];

  function renderFleet() {
    act(() => {
      root.render(<AggregatedPnlChart snapshots={twoBotSnapshots} controllers={controllers} />);
    });
  }

  it("gives each bot its own chip and its own selection key", () => {
    renderFleet();

    expect([...lastFold()].sort()).toEqual([
      "alpha:grid_sol",
      "alpha:pmm",
      "beta:grid_sol",
    ]);
    // The ambiguous id is qualified by its bot; the unique one is left alone.
    expect(chips()).toEqual(["grid_sol · alpha", "grid_sol · beta", "pmm"]);
  });

  it("drops only the clicked bot's series, leaving its namesake folded in", () => {
    renderFleet();
    clickChip("grid_sol · beta");

    expect([...lastFold()].sort()).toEqual(["alpha:grid_sol", "alpha:pmm"]);
  });
});

describe("AggregatedPnlChart selection identity", () => {
  it("keeps the same Set — and folds once — when a frame only brings new objects", () => {
    render(["ctrl-a", "ctrl-b"]);
    expect(folds).toHaveLength(1);
    const first = lastFold();
    expect([...first].sort()).toEqual([key("ctrl-a"), key("ctrl-b")]);

    // Same fleet, three more WS frames: new arrays, new objects, same ids.
    render(["ctrl-a", "ctrl-b"]);
    render(["ctrl-a", "ctrl-b"]);
    render(["ctrl-a", "ctrl-b"]);

    // One fold per frame — no re-sync, so no second render pass — and the very
    // same Set instance throughout.
    expect(folds).toHaveLength(4);
    for (const seen of folds) expect(seen).toBe(first);
  });

  it("keeps the same Set when a controller joins the fleet", () => {
    render(["ctrl-a", "ctrl-b"]);
    const first = lastFold();

    render(["ctrl-a", "ctrl-b", "ctrl-c"]);

    // A new controller prunes nothing, so the selection is unchanged — both in
    // membership (a joiner is not folded in until it is picked) and identity.
    expect(lastFold()).toBe(first);
    expect(chips()).toEqual(["ctrl-a", "ctrl-b", "ctrl-c"]);
  });

  it("drops a controller that left the fleet", () => {
    render(["ctrl-a", "ctrl-b"]);
    render(["ctrl-a"]);

    expect([...lastFold()]).toEqual([key("ctrl-a")]);
    expect(chips()).toEqual([]); // a single controller needs no chip strip
  });

  it("falls back to the whole fleet when nothing in the selection survives", () => {
    render(["ctrl-a", "ctrl-b"]);
    clickChip("ctrl-b"); // user narrows the chart down to ctrl-a
    expect([...lastFold()]).toEqual([key("ctrl-a")]);

    render(["ctrl-b", "ctrl-c"]); // ctrl-a is gone: the selection is now empty

    expect([...lastFold()].sort()).toEqual([key("ctrl-b"), key("ctrl-c")]);
  });

  it("still toggles a controller in and out on click", () => {
    render(["ctrl-a", "ctrl-b"]);

    clickChip("ctrl-a");
    expect([...lastFold()]).toEqual([key("ctrl-b")]);

    clickChip("ctrl-a");
    expect([...lastFold()].sort()).toEqual([key("ctrl-a"), key("ctrl-b")]);

    // The last enabled controller cannot be toggled off — that would leave the
    // chart with nothing to draw.
    clickChip("ctrl-a");
    clickChip("ctrl-b");
    expect([...lastFold()]).toEqual([key("ctrl-b")]);
  });
});
