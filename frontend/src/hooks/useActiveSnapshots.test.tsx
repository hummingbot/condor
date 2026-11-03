/**
 * The fleet chart's snapshot filter, and the frames it must ignore.
 *
 * The promise under test is not "the same rows come out" — that was always
 * true. It is that the *work* does not happen: a `bots` frame that moved only a
 * PnL cell must not walk the fleet's whole performance history again, and must
 * not hand the downstream folds a new array to re-aggregate (PERF-334). Both
 * are asserted the only way they can be asserted from outside — the returned
 * array is referentially identical, and `Date.parse`, which the filter calls
 * once per history row, is not called at all.
 *
 * Against the pre-fix memo — keyed on the `controllers` array, which the socket
 * rebuilds from scratch every ~5s — both of those fail: a fresh array is a new
 * dependency, so the walk re-runs and `.filter()` returns a new array.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";
import { useActiveSnapshots } from "./useActiveSnapshots";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/** A live controller off the `bots` payload. */
const ctrl = (
  bot: string,
  id: string,
  deployedAt: string | null,
  pnl = 0,
): ControllerInfo => ({
  controller_name: id,
  controller_type: "generic",
  controller_id: id,
  bot_name: bot,
  status: "running",
  connector: "binance",
  trading_pair: "SOL-USDC",
  realized_pnl_quote: 0,
  unrealized_pnl_quote: 0,
  global_pnl_quote: pnl,
  global_pnl_pct: 0,
  volume_traded: 0,
  close_type_counts: {},
  positions_summary: [],
  deployed_at: deployedAt,
  config: {},
});

/** One stored row of controller-performance history. */
const snap = (bot: string, id: string, timestamp: string): ControllerPerformanceSnapshot => ({
  timestamp,
  bot_name: bot,
  controller_id: id,
  controller_name: id,
  connector: "binance",
  trading_pair: "SOL-USDC",
  realized_pnl_quote: 0,
  unrealized_pnl_quote: 0,
  global_pnl_quote: 0,
  global_pnl_pct: 0,
  volume_traded: 0,
  positions_summary: [],
});

let container: HTMLDivElement;
let root: Root;
/** Every value the hook has returned, newest last. */
let seen: ControllerPerformanceSnapshot[][];

function Probe({
  snapshots,
  controllers,
}: {
  snapshots: ControllerPerformanceSnapshot[] | undefined;
  controllers: ControllerInfo[];
}) {
  seen.push(useActiveSnapshots(snapshots, controllers));
  return null;
}

function draw(snapshots: ControllerPerformanceSnapshot[] | undefined, controllers: ControllerInfo[]) {
  act(() => {
    root.render(<Probe snapshots={snapshots} controllers={controllers} />);
  });
  return seen[seen.length - 1];
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  seen = [];
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("useActiveSnapshots", () => {
  const history = [
    snap("bot-a", "c1", "2026-09-01T00:00:00Z"),
    snap("bot-a", "c1", "2026-09-02T00:00:00Z"),
    snap("bot-b", "c2", "2026-09-02T00:00:00Z"),
  ];

  it("does no work at all on a bots frame that moved only PnL", () => {
    const roster = [ctrl("bot-a", "c1", "2026-08-31T00:00:00Z"), ctrl("bot-b", "c2", null)];
    const first = draw(history, roster);
    expect(first).toHaveLength(3);

    // The next `bots` frame: a wholly fresh array of wholly fresh objects, as
    // `shared-socket.ts` builds one every ~5s, differing only in live PnL.
    const parse = vi.spyOn(Date, "parse");
    const nextFrame = [
      ctrl("bot-a", "c1", "2026-08-31T00:00:00Z", 12.34),
      ctrl("bot-b", "c2", null, -5.5),
    ];
    expect(nextFrame).not.toBe(roster);
    const second = draw(history, nextFrame);

    // Same array, not merely equal: every downstream fold keyed on
    // `fleet.snapshots` therefore does not re-run either.
    expect(second).toBe(first);
    // And the history was not re-walked: the filter parses a timestamp per row.
    expect(parse).not.toHaveBeenCalled();
  });

  it("re-runs when a controller joins the roster", () => {
    const first = draw(history, [ctrl("bot-a", "c1", null)]);
    expect(first).toHaveLength(2);

    const second = draw(history, [ctrl("bot-a", "c1", null), ctrl("bot-b", "c2", null)]);
    expect(second).not.toBe(first);
    expect(second).toHaveLength(3);
  });

  it("re-runs when a deploy time changes", () => {
    const first = draw(history, [ctrl("bot-a", "c1", "2026-08-31T00:00:00Z")]);
    expect(first).toHaveLength(2);

    const second = draw(history, [ctrl("bot-a", "c1", "2026-09-01T12:00:00Z")]);
    expect(second).not.toBe(first);
    expect(second.map((s) => s.timestamp)).toEqual(["2026-09-02T00:00:00Z"]);
  });

  it("cuts each bot at its own deploy time when two share a controller id (CORR-241)", () => {
    const shared = [
      snap("old-bot", "c1", "2026-09-01T00:00:00Z"),
      snap("old-bot", "c1", "2026-09-05T00:00:00Z"),
      snap("new-bot", "c1", "2026-09-01T00:00:00Z"),
      snap("new-bot", "c1", "2026-09-05T00:00:00Z"),
    ];
    const out = draw(shared, [
      ctrl("old-bot", "c1", "2026-08-30T00:00:00Z"),
      ctrl("new-bot", "c1", "2026-09-04T00:00:00Z"),
    ]);

    expect(out.map((s) => `${s.bot_name}@${s.timestamp}`)).toEqual([
      "old-bot@2026-09-01T00:00:00Z",
      "old-bot@2026-09-05T00:00:00Z",
      "new-bot@2026-09-05T00:00:00Z",
    ]);
  });

  it("drops snapshots whose controller is gone, and keeps a row with no deploy time", () => {
    const out = draw(history, [ctrl("bot-b", "c2", null)]);
    expect(out.map((s) => s.bot_name)).toEqual(["bot-b"]);
  });

  it("returns one stable empty array before any controller has arrived", () => {
    const first = draw(history, []);
    const second = draw(history, []);
    expect(first).toEqual([]);
    expect(second).toBe(first);
  });

  it("returns one stable empty array while the history is still loading", () => {
    const roster = [ctrl("bot-a", "c1", null)];
    const first = draw(undefined, roster);
    const second = draw(undefined, [ctrl("bot-a", "c1", null, 9)]);
    expect(first).toEqual([]);
    expect(second).toBe(first);
  });
});
