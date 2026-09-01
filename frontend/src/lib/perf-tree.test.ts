/**
 * The one fold, pinned (FEAT-086).
 *
 * Every case here is a way the unified browser could report a number nobody
 * earned: a controller counted alongside the executors it already contains, a
 * run history summed into a fleet total it overlaps, a terminated runtime
 * measured to now, a return % averaged across a scope, a win rate quoted over
 * positions that are still open.
 */

import { describe, expect, it } from "vitest";

import type { BotRunInfo, ControllerInfo, ExecutorInfo } from "./api";
import {
  UNATTACHED_BOT,
  ancestorChain,
  buildTree,
  foldLeaves,
  indexTree,
  leafFromBotRun,
  leafFromController,
  leafFromExecutor,
  type PerfLeaf,
  type PerfNode,
} from "./perf-tree";

/** Everything is already in the display currency in these tests. */
const identity = (value: number) => value;

const HOUR = 3_600_000;
const NOW = Date.parse("2026-09-01T12:00:00Z");

function controller(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_id: "pmm_1",
    bot_name: "alpha",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 10,
    unrealized_pnl_quote: 2,
    global_pnl_quote: 12,
    global_pnl_pct: 1.5,
    volume_traded: 1000,
    close_type_counts: { TAKE_PROFIT: 3, STOP_LOSS: 1 },
    positions_summary: [{ trading_pair: "SOL-USDC" }],
    deployed_at: new Date(NOW - 4 * HOUR).toISOString(),
    config: { total_amount_quote: 500 },
    ...over,
  };
}

function executor(over: Partial<ExecutorInfo> = {}): ExecutorInfo {
  return {
    id: "ex1",
    type: "position_executor",
    connector: "binance",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "terminated",
    close_type: "TAKE_PROFIT",
    pnl: 5,
    volume: 400,
    timestamp: (NOW - 3 * HOUR) / 1000,
    controller_id: "pmm_1",
    cum_fees_quote: 0.4,
    net_pnl_pct: 0.02,
    entry_price: 100,
    current_price: 101,
    close_timestamp: (NOW - 2 * HOUR) / 1000,
    custom_info: {},
    config: {},
    ...over,
  };
}

function botRun(over: Partial<BotRunInfo> = {}): BotRunInfo {
  return {
    bot_name: "gamma",
    bot_run_id: 7,
    account_name: "master",
    strategy_type: "generic",
    strategy_name: "gamma_v1",
    run_status: "STOPPED",
    deployment_status: "ARCHIVED",
    created_at: new Date(NOW - 10 * HOUR).toISOString(),
    stopped_at: new Date(NOW - 6 * HOUR).toISOString(),
    realized_pnl_quote: 100,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 100,
    volume_traded: 9000,
    num_controllers: 2,
    archive_db_path: "/data/gamma.sqlite",
    ...over,
  };
}

/** The node at `id`, or a failing assertion naming what was actually built. */
function node(root: PerfNode, id: string): PerfNode {
  const found = indexTree(root).get(id);
  expect(found, `no node "${id}" in ${[...indexTree(root).keys()].join(", ")}`).toBeDefined();
  return found!;
}

describe("leaf adapters", () => {
  it("keeps a controller's own split and its declared capital", () => {
    const leaf = leafFromController(controller());
    expect(leaf.kind).toBe("controller");
    expect(leaf.id).toBe("alpha:pmm_1");
    expect(leaf.realized).toBe(10);
    expect(leaf.unrealized).toBe(2);
    expect(leaf.net).toBe(12);
    expect(leaf.capital).toBe(500);
    expect(leaf.running).toBe(true);
    expect(leaf.endedAt).toBeNull();
    // A controller's class is what `group=type` places it by.
    expect(leaf.executorType).toBe("pmm_simple");
  });

  it("reads a controller as stopped from its kill switch, not its status", () => {
    // The bot-status payload hardcodes "running"; the kill switch is the truth.
    const leaf = leafFromController(
      controller({ status: "running", config: { manual_kill_switch: true } }),
    );
    expect(leaf.running).toBe(false);
    expect(leaf.status).toBe("stopped");
  });

  it("declares no capital when the controller declares none", () => {
    expect(leafFromController(controller({ config: {} })).capital).toBe(0);
    expect(leafFromController(controller({ config: { total_amount_quote: 0 } })).capital).toBe(0);
  });

  it("banks a closed executor's pnl and leaves a live one's unrealized", () => {
    const closed = leafFromExecutor(executor());
    expect(closed.realized).toBe(5);
    expect(closed.unrealized).toBe(0);
    expect(closed.running).toBe(false);
    expect(closed.endedAt).toBe(NOW - 2 * HOUR);

    const live = leafFromExecutor(executor({ status: "active", close_timestamp: 0, close_type: "" }));
    expect(live.realized).toBe(0);
    expect(live.unrealized).toBe(5);
    expect(live.running).toBe(true);
    expect(live.endedAt).toBeNull();
    expect(live.closeTypes).toEqual({});
  });

  it("gives an executor with no known bot one of its own", () => {
    expect(leafFromExecutor(executor()).bot).toBe(UNATTACHED_BOT);
    expect(leafFromExecutor(executor(), "alpha").bot).toBe("alpha");
  });

  it("turns an executor's close type into a one-entry histogram", () => {
    expect(leafFromExecutor(executor()).closeTypes).toEqual({ TAKE_PROFIT: 1 });
  });

  it("identifies two runs of the same bot separately", () => {
    const first = leafFromBotRun(botRun());
    const second = leafFromBotRun(
      botRun({ created_at: new Date(NOW - 40 * HOUR).toISOString() }),
    );
    expect(first.id).not.toBe(second.id);
    expect(first.endedAt).toBe(NOW - 6 * HOUR);
    expect(first.running).toBe(false);
  });
});

describe("buildTree", () => {
  it("hangs a running executor under its controller without double counting it", () => {
    const leaves = [
      leafFromController(controller()),
      leafFromExecutor(executor({ id: "a", status: "active", close_timestamp: 0 }), "alpha"),
      leafFromExecutor(executor({ id: "b", status: "active", close_timestamp: 0 }), "alpha"),
    ];
    const tree = buildTree(leaves, "bot");
    const ctrl = node(tree, "ctrl:alpha:pmm_1");

    // The controller record is authoritative: it already contains every
    // executor it has ever run, including ones that closed and were never
    // loaded. Its children are drill-in, not addends.
    expect(ctrl.leaves).toHaveLength(1);
    expect(ctrl.leaves[0].kind).toBe("controller");
    expect(ctrl.children.map((c) => c.id)).toEqual(["exec:a", "exec:b"]);

    const fleet = foldLeaves(tree.leaves, identity, NOW);
    expect(fleet.net).toBe(12);
    expect(fleet.count).toBe(1);
  });

  it("folds a controller with no record of its own out of its executors", () => {
    // The terminated population: archived executors and no live controller.
    const leaves = [
      leafFromExecutor(executor({ id: "a", pnl: 5, volume: 400 })),
      leafFromExecutor(executor({ id: "b", pnl: -2, volume: 100 })),
    ];
    const tree = buildTree(leaves, "bot");
    const ctrl = node(tree, `ctrl:${UNATTACHED_BOT}:pmm_1`);
    expect(ctrl.leaves).toHaveLength(2);
    expect(foldLeaves(ctrl.leaves, identity, NOW).net).toBe(3);
  });

  it("keeps the run history beside the fleet rather than inside it", () => {
    const leaves = [leafFromExecutor(executor({ pnl: 5 })), leafFromBotRun(botRun())];
    const tree = buildTree(leaves, "bot");
    const runs = node(tree, "runs");

    expect(runs.rollsUp).toBe(false);
    expect(foldLeaves(runs.leaves, identity, NOW).net).toBe(100);
    // A run's totals and the archived executors' totals describe overlapping
    // trading that cannot be de-duplicated here, so the fleet counts one of
    // them — the executors — and the runs branch reports itself.
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(5);
    expect(tree.leaves).toHaveLength(1);
  });

  it("puts a run in the same place under either grouping", () => {
    const leaves = [leafFromBotRun(botRun())];
    for (const groupBy of ["bot", "type"] as const) {
      const tree = buildTree(leaves, groupBy);
      expect(tree.children.map((c) => c.id)).toEqual(["runs"]);
    }
  });

  it("groups by bot or by class, and keeps controller ids identical in both", () => {
    const leaves = [
      leafFromController(controller({ bot_name: "alpha", controller_id: "pmm_1" })),
      leafFromController(
        controller({ bot_name: "beta", controller_id: "grid_1", controller_name: "grid_strike" }),
      ),
    ];

    const byBot = buildTree(leaves, "bot");
    expect(byBot.children.map((c) => c.id)).toEqual(["bot:alpha", "bot:beta"]);

    const byType = buildTree(leaves, "type");
    expect(byType.children.map((c) => c.id)).toEqual(["type:pmm_simple", "type:grid_strike"]);

    // The level in between changed; the controller nodes did not, which is what
    // lets a selection survive the switch.
    for (const tree of [byBot, byType]) {
      expect(node(tree, "ctrl:alpha:pmm_1").leaves).toHaveLength(1);
      expect(node(tree, "ctrl:beta:grid_1").leaves).toHaveLength(1);
      expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(24);
    }
  });

  it("hangs an executor belonging to no controller under its group directly", () => {
    const leaves = [leafFromExecutor(executor({ id: "loose", controller_id: "" }))];
    const tree = buildTree(leaves, "bot");
    const bot = node(tree, `bot:${UNATTACHED_BOT}`);
    expect(bot.children.map((c) => c.id)).toEqual(["exec:loose"]);
    expect(bot.leaves).toHaveLength(1);
  });

  it("attaches a controller leaf to a node its executors opened first", () => {
    const leaves = [
      leafFromExecutor(executor({ id: "a", status: "active", close_timestamp: 0 }), "alpha"),
      leafFromController(controller()),
    ];
    const ctrl = node(buildTree(leaves, "bot"), "ctrl:alpha:pmm_1");
    expect(ctrl.leaves.map((l) => l.kind)).toEqual(["controller"]);
    expect(ctrl.children).toHaveLength(1);
  });
});

describe("ancestorChain", () => {
  const tree = buildTree(
    [
      leafFromController(controller()),
      leafFromExecutor(executor({ id: "a", status: "active", close_timestamp: 0 }), "alpha"),
    ],
    "bot",
  );

  it("walks from a node up to the fleet", () => {
    expect(ancestorChain(tree, "exec:a")).toEqual([
      "exec:a",
      "ctrl:alpha:pmm_1",
      "bot:alpha",
      "all",
    ]);
  });

  it("is empty for a node that is not in the tree", () => {
    expect(ancestorChain(tree, "exec:gone")).toEqual([]);
  });

  it("names the nearest surviving ancestor after a population switch", () => {
    // The executor is gone from the next tree; its controller is not.
    const chain = ancestorChain(tree, "exec:a");
    const next = indexTree(buildTree([leafFromController(controller())], "bot"));
    expect(chain.find((id) => next.has(id))).toBe("ctrl:alpha:pmm_1");
  });
});

describe("foldLeaves", () => {
  it("adds up a controller's executors to the controller's own numbers", () => {
    // The acceptance criterion, asserted rather than eyeballed: a controller is
    // a bag of executors, so folding the bag reproduces the bag's label.
    const parts = [
      executor({ id: "a", pnl: 7, volume: 400, cum_fees_quote: 0.4, close_type: "TAKE_PROFIT" }),
      executor({ id: "b", pnl: 3, volume: 250, cum_fees_quote: 0.25, close_type: "TAKE_PROFIT" }),
      executor({ id: "c", pnl: -2, volume: 350, cum_fees_quote: 0.35, close_type: "STOP_LOSS" }),
    ];
    const leaves = parts.map((e) => leafFromExecutor(e, "alpha"));
    const whole = foldLeaves(leaves, identity, NOW);

    expect(whole.net).toBe(8);
    expect(whole.realized).toBe(8);
    expect(whole.unrealized).toBe(0);
    expect(whole.volume).toBe(1000);
    expect(whole.fees).toBeCloseTo(1, 10);
    expect(whole.closeTypes).toEqual([
      ["TAKE_PROFIT", 2],
      ["STOP_LOSS", 1],
    ]);
    expect(whole.closeTotal).toBe(3);

    // And the tree's controller node reports exactly that fold.
    const ctrl = node(buildTree(leaves, "bot"), "ctrl:alpha:pmm_1");
    expect(foldLeaves(ctrl.leaves, identity, NOW)).toEqual(whole);
  });

  it("converts every leaf through its own pair", () => {
    const cv = (value: number, pair: string) => (pair.endsWith("-EUR") ? value * 2 : value);
    const leaves = [
      leafFromExecutor(executor({ id: "a", pnl: 10, trading_pair: "SOL-USDC" })),
      leafFromExecutor(executor({ id: "b", pnl: 10, trading_pair: "SOL-EUR" })),
    ];
    expect(foldLeaves(leaves, cv, NOW).net).toBe(30);
  });

  it("measures a terminated runtime to the last close, not to now", () => {
    const leaves = [
      leafFromExecutor(
        executor({
          id: "a",
          timestamp: (NOW - 10 * HOUR) / 1000,
          close_timestamp: (NOW - 8 * HOUR) / 1000,
        }),
      ),
      leafFromExecutor(
        executor({
          id: "b",
          timestamp: (NOW - 9 * HOUR) / 1000,
          close_timestamp: (NOW - 6 * HOUR) / 1000,
        }),
      ),
    ];
    expect(foldLeaves(leaves, identity, NOW).hours).toBe(4);
  });

  it("runs a live fold to now, however long ago its closed leaves ended", () => {
    const leaves = [
      leafFromExecutor(
        executor({
          id: "a",
          timestamp: (NOW - 10 * HOUR) / 1000,
          close_timestamp: (NOW - 8 * HOUR) / 1000,
        }),
      ),
      leafFromExecutor(
        executor({ id: "b", status: "active", timestamp: (NOW - 5 * HOUR) / 1000, close_timestamp: 0 }),
      ),
    ];
    expect(foldLeaves(leaves, identity, NOW).hours).toBe(10);
  });

  it("reports no runtime when nothing in scope says when it started", () => {
    const leaves = [leafFromExecutor(executor({ timestamp: 0, close_timestamp: 0, status: "active" }))];
    expect(foldLeaves(leaves, identity, NOW).hours).toBe(0);
  });

  it("reports no runtime for a closed fold that lost its close times", () => {
    // Measuring one of these to `now` would grow a runtime for trading that
    // stopped, and shrink every per-hour pace derived from it.
    const leaves = [leafFromExecutor(executor({ close_timestamp: 0 }))];
    expect(foldLeaves(leaves, identity, NOW).hours).toBe(0);
  });

  it("counts a win rate over what has closed, and nothing when nothing has", () => {
    const closed = [
      leafFromExecutor(executor({ id: "a", pnl: 5 })),
      leafFromExecutor(executor({ id: "b", pnl: -1 })),
      leafFromExecutor(executor({ id: "c", pnl: 0 })),
    ];
    const fold = foldLeaves(closed, identity, NOW);
    expect(fold.closed).toBe(3);
    expect(fold.wins).toBe(1);
    expect(fold.winRate).toBeCloseTo(1 / 3, 10);

    const live = [leafFromExecutor(executor({ status: "active", close_timestamp: 0, pnl: 9 }))];
    const liveFold = foldLeaves(live, identity, NOW);
    expect(liveFold.closed).toBe(0);
    expect(liveFold.winRate).toBeUndefined();
  });

  it("counts the closed subset's win rate inside a mixed fold", () => {
    const leaves = [
      leafFromExecutor(executor({ id: "a", pnl: 5 })),
      leafFromExecutor(executor({ id: "b", status: "active", close_timestamp: 0, pnl: 3 })),
    ];
    const fold = foldLeaves(leaves, identity, NOW);
    expect(fold.count).toBe(2);
    expect(fold.closed).toBe(1);
    expect(fold.winRate).toBe(1);
  });

  it("carries a return % for one leaf and never for a fold of many", () => {
    const one = leafFromController(controller({ global_pnl_pct: 1.5 }));
    expect(foldLeaves([one], identity, NOW).returnPct).toBe(1.5);
    const two = [one, leafFromController(controller({ controller_id: "pmm_2", global_pnl_pct: 9 }))];
    expect(foldLeaves(two, identity, NOW).returnPct).toBeUndefined();
  });

  it("sums capital and counts open positions and bots", () => {
    const leaves: PerfLeaf[] = [
      leafFromController(controller({ bot_name: "alpha" })),
      leafFromController(controller({ bot_name: "beta", controller_id: "pmm_2" })),
    ];
    const fold = foldLeaves(leaves, identity, NOW);
    expect(fold.capital).toBe(1000);
    expect(fold.positions).toBe(2);
    expect(fold.bots).toBe(2);
  });

  it("folds nothing into zeroes rather than into NaN", () => {
    const fold = foldLeaves([], identity, NOW);
    expect(fold).toMatchObject({ net: 0, volume: 0, count: 0, closed: 0, hours: 0, closeTotal: 0 });
    expect(fold.winRate).toBeUndefined();
    expect(fold.returnPct).toBeUndefined();
  });
});
