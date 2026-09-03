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

import { BEFORE_LEDGER, OUTSIDE } from "@/components/perf/agentFilter";
import type { DeedIndex } from "@/lib/agent-attribution";
import type { BotRunInfo, ControllerInfo, ExecutorInfo } from "./api";
import {
  AUTO_OPEN_KINDS,
  UNATTACHED_BOT,
  agentNodeId,
  agentOfNodeId,
  ancestorChain,
  autoOpenIds,
  botNodeId,
  botOfNodeId,
  buildTree,
  controllerClassOf,
  controllerNodeId,
  countNodes,
  foldLeaves,
  matchesGrain,
  groupNodeId,
  indexTree,
  leafFromController,
  leafFromExecutor,
  leafFromTerminatedController,
  resolveScope,
  runStatus,
  visibleNodeIds,
  type Grain,
  type GroupAxis,
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
    controller_type: "",
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
    controller_ids: ["gamma_1", "gamma_2"],
    is_live: false,
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
    expect(leaf.status).toBe("stopped");
    // Paused, not finished: this payload only describes live controllers, and a
    // fold that thought otherwise would report no runtime for a paused bot.
    expect(leaf.running).toBe(true);
    expect(foldLeaves([leaf], identity, NOW).hours).toBe(4);
  });

  it("declares no capital when the controller declares none", () => {
    expect(leafFromController(controller({ config: {} })).capital).toBe(0);
    expect(leafFromController(controller({ config: { total_amount_quote: 0 } })).capital).toBe(0);
  });

  // `leafFromController` hardcodes `running: true, endedAt: null`, which is
  // right for a payload that only describes live controllers. Inheriting it for
  // a finished one is not cosmetic: `foldLeaves` runs a scope's clock to *now*
  // while anything in it is running, so the run would grow a runtime for
  // trading that stopped last week and every per-hour pace would shrink to
  // match.
  it("ends a finished controller at its run's stop, and does not call it running", () => {
    const leaf = leafFromTerminatedController(
      controller({ deployed_at: new Date(NOW - 10 * HOUR).toISOString() }),
      botRun({ stopped_at: new Date(NOW - 6 * HOUR).toISOString() }),
    );
    expect(leaf.running).toBe(false);
    expect(leaf.status).toBe("stopped");
    expect(leaf.startedAt).toBe(NOW - 10 * HOUR);
    expect(leaf.endedAt).toBe(NOW - 6 * HOUR);

    const fold = foldLeaves([leaf], identity, NOW);
    expect(fold.hours).toBe(4);
  });

  it("keeps a finished controller's own pair, so its quote is not folded as dollars", () => {
    const leaf = leafFromTerminatedController(controller({ trading_pair: "BTC-BRL" }));
    expect(leaf.pair).toBe("BTC-BRL");
    expect(leaf.closeTypes).toEqual({ TAKE_PROFIT: 3, STOP_LOSS: 1 });
  });

  it("has no end when no run says when its bot stopped", () => {
    expect(leafFromTerminatedController(controller()).endedAt).toBeNull();
  });

  it("keeps the same id as the live controller it used to be", () => {
    expect(leafFromTerminatedController(controller()).id).toBe(
      leafFromController(controller()).id,
    );
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

  it("gives an executor with no known bot the controller id it carries, or one of its own", () => {
    expect(leafFromExecutor(executor()).bot).toBe("pmm_1");
    expect(leafFromExecutor(executor({ controller_id: "" })).bot).toBe(UNATTACHED_BOT);
    expect(leafFromExecutor(executor(), "alpha").bot).toBe("alpha");
  });

  it("turns an executor's close type into a one-entry histogram", () => {
    expect(leafFromExecutor(executor()).closeTypes).toEqual({ TAKE_PROFIT: 1 });
  });

  // Upstream never writes `RUNNING`. Over 150 real runs the only values are
  // `STOPPED` and `CREATED`, and every bot trading right now is a `CREATED`
  // one — so reading that column called the live fleet finished and showed it
  // a status dot reading "created".
  it("reads a run as live from its deployment, not from run_status", () => {
    const live = botRun({ run_status: "CREATED", deployment_status: "DEPLOYED", stopped_at: null, is_live: true });
    expect(runStatus(live)).toBe("running");
  });

  it("reads a run with a stop time as stopped, whatever the column says", () => {
    expect(runStatus(botRun({ run_status: "CREATED" }))).toBe("stopped");
    expect(runStatus(botRun({ run_status: "STOPPED" }))).toBe("stopped");
  });

  it("falls back to the reported status only when nothing else says", () => {
    expect(runStatus(botRun({ run_status: "FAILED", stopped_at: null }))).toBe("failed");
  });
});

describe("buildTree", () => {
  it("hangs a running executor under its controller without double counting it", () => {
    const leaves = [
      leafFromController(controller()),
      leafFromExecutor(executor({ id: "a", status: "active", close_timestamp: 0 }), "alpha"),
      leafFromExecutor(executor({ id: "b", status: "active", close_timestamp: 0 }), "alpha"),
    ];
    const tree = buildTree(leaves);
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
    // The terminated population: archived executors of a run that is over,
    // attributed to their bot by its run window, and no live controller.
    const leaves = [
      leafFromExecutor(executor({ id: "a", pnl: 5, volume: 400 }), "alpha"),
      leafFromExecutor(executor({ id: "b", pnl: -2, volume: 100 }), "alpha"),
    ];
    const tree = buildTree(leaves);
    const ctrl = node(tree, "ctrl:alpha:pmm_1");
    expect(ctrl.leaves).toHaveLength(2);
    expect(foldLeaves(ctrl.leaves, identity, NOW).net).toBe(3);
  });

  // The `runs` branch is gone (FEAT-089), and so is the bot level that replaced
  // it: a finished run's controllers sit in the fleet's list exactly as a live
  // bot's do. The branch's `rollsUp: false` guard is not needed, because the
  // trading arrives as controller leaves and the spine rule already stops a
  // controller being counted alongside its own executors.
  it("builds a finished run as controllers of the fleet, not a branch beside it", () => {
    const leaves = [
      leafFromTerminatedController(
        controller({ bot_name: "gamma", controller_id: "gamma_1", global_pnl_quote: 60 }),
        botRun(),
      ),
      leafFromTerminatedController(
        controller({ bot_name: "gamma", controller_id: "gamma_2", global_pnl_quote: 40 }),
        botRun(),
      ),
    ];
    const tree = buildTree(leaves);

    expect(tree.children.map((c) => c.id)).toEqual([
      "ctrl:gamma:gamma_1",
      "ctrl:gamma:gamma_2",
    ]);
    // The run's total reaches the fleet, which the `runs` branch deliberately
    // never did.
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(100);
  });

  // The guard the deleted branch existed to provide, proved to be unnecessary:
  // a controller node folds its own record and its executor children are
  // drill-in, so a finished run's trading is counted once even with its closed
  // executors hanging underneath it.
  it("counts a finished run's trading once, not once per level", () => {
    const leaves = [
      leafFromTerminatedController(
        controller({ bot_name: "gamma", controller_id: "gamma_1", global_pnl_quote: 60 }),
        botRun(),
      ),
      leafFromExecutor(executor({ id: "e1", pnl: 25, controller_id: "gamma_1" }), "gamma"),
      leafFromExecutor(executor({ id: "e2", pnl: 35, controller_id: "gamma_1" }), "gamma"),
    ];
    const tree = buildTree(leaves);
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(60);
  });

  // The executors that belong to no run are disjoint from every controller
  // record by construction — on a real server they are the positions opened by
  // hand — so folding both into one fleet total counts nothing twice.
  it("adds unattached executors to the fleet beside the runs, once each", () => {
    const leaves = [
      leafFromTerminatedController(
        controller({ bot_name: "gamma", controller_id: "gamma_1", global_pnl_quote: 60 }),
        botRun(),
      ),
      leafFromExecutor(executor({ id: "manual", pnl: 5, controller_id: "main" })),
    ];
    const tree = buildTree(leaves);
    expect(tree.children.map((c) => c.id)).toEqual(["ctrl:gamma:gamma_1", "exec:manual"]);
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(65);
  });

  // The id a hand-opened executor carries names the executor, not a controller:
  // there is nothing behind `main` for a controller row to stand for, so the
  // id is promoted to the bot the executor is filed under and the row goes.
  it("files an executor nobody claims under its controller id, off the fleet directly", () => {
    const leaf = leafFromExecutor(executor({ id: "manual", controller_id: "main" }));
    expect(leaf.bot).toBe("main");
    expect(leaf.controllerId).toBe("");
    expect(controllerNodeId(leaf)).toBeNull();
    expect(buildTree([leaf]).children.map((c) => c.id)).toEqual(["exec:manual"]);
  });

  it("keeps an executor's controller when a bot claims it", () => {
    const leaf = leafFromExecutor(executor({ id: "e", controller_id: "pmm_1" }), "alpha");
    expect(leaf.bot).toBe("alpha");
    expect(leaf.controllerId).toBe("pmm_1");
    expect(controllerNodeId(leaf)).toBe("ctrl:alpha:pmm_1");
  });

  // The grouping level is gone: two bots' controllers are siblings in one list,
  // and which bot a row belongs to is a bubble above the tree rather than a
  // chevron the reader walks through.
  it("puts every controller in one list, whichever bot ran it", () => {
    const leaves = [
      leafFromController(controller({ bot_name: "alpha", controller_id: "pmm_1" })),
      leafFromController(
        controller({ bot_name: "beta", controller_id: "grid_1", controller_name: "grid_strike" }),
      ),
    ];

    const tree = buildTree(leaves);
    expect(tree.children.map((c) => c.id)).toEqual(["ctrl:alpha:pmm_1", "ctrl:beta:grid_1"]);
    expect(node(tree, "ctrl:alpha:pmm_1").leaves).toHaveLength(1);
    expect(node(tree, "ctrl:beta:grid_1").leaves).toHaveLength(1);
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(24);
  });

  it("hangs an executor belonging to no controller off the fleet directly", () => {
    const leaves = [leafFromExecutor(executor({ id: "loose", controller_id: "" }))];
    const tree = buildTree(leaves);
    expect(tree.children.map((c) => c.id)).toEqual(["exec:loose"]);
    expect(tree.leaves).toHaveLength(1);
  });

  it("attaches a controller leaf to a node its executors opened first", () => {
    const leaves = [
      leafFromExecutor(executor({ id: "a", status: "active", close_timestamp: 0 }), "alpha"),
      leafFromController(controller()),
    ];
      const ctrl = node(buildTree(leaves), "ctrl:alpha:pmm_1");
    expect(ctrl.leaves.map((l) => l.kind)).toEqual(["controller"]);
    expect(ctrl.children).toHaveLength(1);
  });
});

describe("buildTree, grouping by bot", () => {
  const twoBots = () => [
    leafFromController(controller({ bot_name: "alpha", controller_id: "pmm_1" })),
    leafFromController(controller({ bot_name: "alpha", controller_id: "pmm_2" })),
    leafFromController(
      controller({ bot_name: "beta", controller_id: "grid_1", controller_name: "grid_strike" }),
    ),
  ];

  it("puts each bot's controllers under a row of its own", () => {
    const tree = buildTree(twoBots(), "All", { grouping: ["bot"] });

    expect(tree.children.map((c) => c.id)).toEqual(["bot:alpha", "bot:beta"]);
    expect(node(tree, "bot:alpha").children.map((c) => c.id)).toEqual([
      "ctrl:alpha:pmm_1",
      "ctrl:alpha:pmm_2",
    ]);
    expect(node(tree, "bot:beta").children.map((c) => c.id)).toEqual(["ctrl:beta:grid_1"]);
  });

  // The Stop button on the row posts this, so it must be the name the API
  // knows rather than the shortened one the sidebar draws.
  it("labels a bot row with the bot's full name", () => {
    const tree = buildTree(
      [leafFromController(controller({ bot_name: "hummingbot-alpha-1" }))],
      "All",
      { grouping: ["bot"] },
    );
    expect(node(tree, "bot:hummingbot-alpha-1").label).toBe("hummingbot-alpha-1");
    expect(botOfNodeId("bot:hummingbot-alpha-1")).toBe("hummingbot-alpha-1");
    expect(botOfNodeId("ctrl:hummingbot-alpha-1:pmm_1")).toBe("hummingbot-alpha-1");
    expect(botOfNodeId("exec:abc")).toBeNull();
  });

  // The level must not become a second place the same trading is counted: a
  // bot node carries no leaf of its own, so it folds its controllers' spines.
  it("folds a bot out of its controllers, once each", () => {
    const tree = buildTree(twoBots(), "All", { grouping: ["bot"] });
    expect(foldLeaves(node(tree, "bot:alpha").leaves, identity, NOW).net).toBe(24);
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(36);
  });

  // A hand-opened position belongs to no deployment, so there is still no bot
  // to stop — which is why it gets a `grp:` row and not a `bot:` one, rather
  // than why it used to get no row at all. That reason is spent: the bucket is
  // a kind of its own, and no code path offers a bot's actions to it.
  it("gathers the executors that belong to no controller under one group row", () => {
    const leaf = leafFromExecutor(executor({ id: "manual", controller_id: "main" }));
    const other = leafFromExecutor(executor({ id: "manual_2", controller_id: "main" }));
    expect(botNodeId(leaf)).toBeNull();
    expect(groupNodeId(leaf)).toBe("grp:main");
    const tree = buildTree(
      [leafFromController(controller()), leaf, other],
      "All",
      { grouping: ["bot"] },
    );
    // The bucket itself nests under the fleet's one "Unattached" row rather
    // than sitting beside the bots directly (see the next describe block).
    expect(tree.children.map((c) => c.id)).toEqual(["bot:alpha", "orphans"]);
    expect(node(tree, "orphans").children.map((c) => c.id)).toEqual(["grp:main"]);
    const group = node(tree, "grp:main");
    expect(group.kind).toBe("group");
    expect(group.label).toBe("main");
    expect(group.children.map((c) => c.id)).toEqual(["exec:manual", "exec:manual_2"]);
  });

  // The bucket must not become a second place the same trading is counted: it
  // carries no leaf of its own, so it folds its executors' spines, once each.
  it("folds a group out of its executors and leaves the fleet total alone", () => {
    const loose = [
      leafFromExecutor(executor({ id: "manual", pnl: 5, controller_id: "main" })),
      leafFromExecutor(executor({ id: "manual_2", pnl: 7, controller_id: "main" })),
    ];
    const leaves = [leafFromController(controller()), ...loose];
    const grouped = buildTree(leaves, "All", { grouping: ["bot"] });
    expect(foldLeaves(node(grouped, "grp:main").leaves, identity, NOW).net).toBe(12);
    // The same leaves, grouped and flat, add up to the same fleet.
    expect(foldLeaves(grouped.leaves, identity, NOW).net).toBe(
      foldLeaves(buildTree(leaves).leaves, identity, NOW).net,
    );
  });

  // The whole reason for a prefix of its own: `Select all` reads this, and a
  // bucket counted as a bot would report one bot more than the fleet runs.
  it("keeps a group out of the bot count and off the bot actions", () => {
    const tree = buildTree(
      [
        leafFromController(controller()),
        leafFromExecutor(executor({ id: "manual", controller_id: "main" })),
      ],
      "All",
      { grouping: ["bot"] },
    );
    expect(countNodes(tree, "bot")).toBe(1);
    expect(countNodes(tree, "group")).toBe(1);
    // What the sidebar's Stop button and the header's run actions ask, and the
    // answer that keeps both off the row.
    expect(botOfNodeId("grp:main")).toBeNull();
  });

  // Keyboard navigation walks what is drawn, so a shut bucket must hide its
  // executors — that is the collapse the 42 flat rows never had. Two shut
  // levels now, not one: the "Unattached" row hides every dead controller id,
  // and each of those still hides its own executors until opened in turn.
  it("walks the unattached row and its buckets before their executors, hiding what is shut", () => {
    const tree = buildTree(
      [
        leafFromController(controller()),
        leafFromExecutor(executor({ id: "manual", controller_id: "main" })),
      ],
      "All",
      { grouping: ["bot"] },
    );
    expect(visibleNodeIds(tree, new Set(["all"]))).toEqual(["all", "bot:alpha", "orphans"]);
    expect(visibleNodeIds(tree, new Set(["all", "orphans"]))).toEqual([
      "all",
      "bot:alpha",
      "orphans",
      "grp:main",
    ]);
    expect(visibleNodeIds(tree, new Set(["all", "orphans", "grp:main"]))).toEqual([
      "all",
      "bot:alpha",
      "orphans",
      "grp:main",
      "exec:manual",
    ]);
  });

  // An executor row a filter removed lands on the bucket it was sitting in
  // rather than all the way back on the fleet. The chain is read in the same
  // order the tree was built in — a fallback that assumed a nesting the tree
  // does not have would name candidates that are not in it (FEAT-107).
  it("falls back from a lost executor to its group when the tree has one", () => {
    const leaf = leafFromExecutor(executor({ id: "manual", controller_id: "main" }));
    const grouping = ["bot"] as const;
    const grouped = indexTree(
      buildTree(
        [leafFromExecutor(executor({ id: "other", controller_id: "main" }))],
        "All",
        { grouping },
      ),
    );
    expect(resolveScope(grouped, "exec:manual", leaf, { grouping })).toBe("grp:main");

    // ...and all the way back when the tree draws no group level at all.
    const flat = indexTree(
      buildTree([leafFromExecutor(executor({ id: "other", controller_id: "main" }))]),
    );
    expect(resolveScope(flat, "exec:manual", leaf, { grouping: [] })).toBe("all");
  });

  // A group is shallower than the executors it holds, so it must never fall
  // *into* one of them — a single position is a much narrower report.
  it("never resolves a group scope down into one of its executors", () => {
    const other = indexTree(
      buildTree(
        [leafFromExecutor(executor({ id: "other", controller_id: "hand" }))],
        "All",
        { grouping: ["bot"] },
      ),
    );
    expect(resolveScope(other, "grp:main")).toBe("all");
  });

  it("counts controllers wherever they sit, flat or grouped", () => {
    expect(countNodes(buildTree(twoBots()), "controller")).toBe(3);
    expect(countNodes(buildTree(twoBots(), "All", { grouping: ["bot"] }), "controller")).toBe(3);
    expect(countNodes(buildTree(twoBots(), "All", { grouping: ["bot"] }), "bot")).toBe(2);
    expect(countNodes(buildTree(twoBots()), "bot")).toBe(0);
  });

  it("walks bot rows before their controllers, and hides shut ones", () => {
    const tree = buildTree(twoBots(), "All", { grouping: ["bot"] });
    expect(visibleNodeIds(tree, new Set(["all"]))).toEqual(["all", "bot:alpha", "bot:beta"]);
    expect(visibleNodeIds(tree, new Set(["all", "bot:alpha"]))).toEqual([
      "all",
      "bot:alpha",
      "ctrl:alpha:pmm_1",
      "ctrl:alpha:pmm_2",
      "bot:beta",
    ]);
  });

  // A filter that removes a controller should leave the reader on the bot that
  // ran it, not all the way back on the fleet.
  it("falls back from a lost controller to its bot when the tree has one", () => {
    const grouped = indexTree(
      buildTree(
        [leafFromController(controller({ controller_id: "other" }))],
        "All",
        { grouping: ["bot"] },
      ),
    );
    expect(resolveScope(grouped, "ctrl:alpha:pmm_1")).toBe("bot:alpha");

    // ...and all the way back when it does not, which is the flat tree's answer.
    const flat = indexTree(buildTree([leafFromController(controller({ controller_id: "other" }))]));
    expect(resolveScope(flat, "ctrl:alpha:pmm_1")).toBe("all");
  });

  // A bot scope is shallower than a controller, so it must never fall *into*
  // one of its own controllers — that is a much narrower report.
  it("never resolves a bot scope down into one of its controllers", () => {
    const other = indexTree(
      buildTree([leafFromController(controller({ bot_name: "beta" }))], "All", { grouping: ["bot"] }),
    );
    expect(resolveScope(other, "bot:alpha")).toBe("all");
  });

  it("walks a controller up through its bot to the fleet", () => {
    const tree = buildTree(twoBots(), "All", { grouping: ["bot"] });
    expect(ancestorChain(tree, "ctrl:alpha:pmm_2")).toEqual([
      "ctrl:alpha:pmm_2",
      "bot:alpha",
      "all",
    ]);
  });
});

/**
 * The agent level (FEAT-096).
 *
 * The fold is untouched by it — an agent node carries no leaf of its own, so it
 * folds its children's spines exactly as a bot node does — and the two ways an
 * agent's work reaches the fleet page land in the two different places the
 * structure has for them: a bot it deployed nests under it, and a standalone
 * executor it created, which has no controller and therefore no bot, hangs off
 * it directly instead of off the fleet root under a raw session id.
 */
describe("buildTree, grouping by agent", () => {
  const RUN_KEY = "brigado.brl_mm";

  /** A bot-mode agent's two controllers, plus a bot nobody owns. */
  const mixedFleet = () => [
    leafFromController(
      controller({ bot_name: "brigado-brl_mm-btc", controller_id: "pmm_1" }),
      RUN_KEY,
    ),
    leafFromController(
      controller({ bot_name: "brigado-brl_mm-btc", controller_id: "pmm_2" }),
      RUN_KEY,
    ),
    leafFromController(controller({ bot_name: "hand-rolled", controller_id: "grid_1" })),
  ];

  // The unowned bot gets an owner row too, and that is FEAT-107's whole point:
  // FEAT-106 split "nobody" into two answers a reader can act on, so ownership
  // can be the spine of the page rather than a level that only some records
  // reach. The bucket sorts *after* the named run — it is what the fleet map
  // could not credit, not one more owner.
  it("nests an agent's bots under it and an unowned bot under its bucket", () => {
    const tree = buildTree(mixedFleet(), "All", { grouping: ["agent", "bot"] });

    expect(tree.children.map((c) => c.id)).toEqual([
      `agent:${RUN_KEY}`,
      `agent:${BEFORE_LEDGER}`,
    ]);
    expect(node(tree, `agent:${BEFORE_LEDGER}`).children.map((c) => c.id)).toEqual([
      "bot:hand-rolled",
    ]);
    expect(node(tree, `agent:${RUN_KEY}`).children.map((c) => c.id)).toEqual([
      "bot:brigado-brl_mm-btc",
    ]);
    expect(node(tree, "bot:brigado-brl_mm-btc").children.map((c) => c.id)).toEqual([
      "ctrl:brigado-brl_mm-btc:pmm_1",
      "ctrl:brigado-brl_mm-btc:pmm_2",
    ]);
  });

  // The executor-mode half of the same requirement. This row is the one the
  // page could not explain before: it used to hang off the fleet root under the
  // literal string `brigado.brl_mm_7`, as if a session id were a bot name.
  it("hangs a standalone executor off its agent, not off the fleet", () => {
    const standalone = leafFromExecutor(
      executor({ id: "ex_1", controller_id: `${RUN_KEY}_7` }),
      UNATTACHED_BOT,
      RUN_KEY,
    );
    expect(botNodeId(standalone)).toBeNull();
    expect(agentNodeId(standalone)).toBe(`agent:${RUN_KEY}`);

    const tree = buildTree([standalone], "All", { grouping: ["agent", "bot"] });
    expect(tree.children.map((c) => c.id)).toEqual([`agent:${RUN_KEY}`]);
    expect(node(tree, `agent:${RUN_KEY}`).children.map((c) => c.id)).toEqual(["exec:ex_1"]);
  });

  // Nobody's executor gets the bucket rather than the fleet root, and the agent
  // is still asked first: an owner is a better answer than a filing key, so an
  // executor an agent created never reaches the bucket to begin with.
  // An executor nobody claims still gets the bucket rather than a bare row, and
  // the bucket now sits *inside* the owner row that says nobody claimed it —
  // ARCH-318's gathering is not spent, it is one level deeper. A real owner is
  // still the better answer and skips the bucket entirely (the case above).
  it("puts an executor that belongs to nobody in the group, inside its owner bucket", () => {
    const manual = leafFromExecutor(executor({ id: "manual", controller_id: "main" }));
    expect(agentNodeId(manual)).toBeNull();
    const tree = buildTree([manual], "All", { grouping: ["agent", "bot"] });
    const bucket = `agent:${BEFORE_LEDGER}`;
    expect(tree.children.map((c) => c.id)).toEqual([bucket]);
    expect(node(tree, bucket).children.map((c) => c.id)).toEqual([`${bucket}>orphans`]);
    expect(node(tree, `${bucket}>orphans`).children.map((c) => c.id)).toEqual([
      `${bucket}>grp:main`,
    ]);
    expect(node(tree, `${bucket}>grp:main`).children.map((c) => c.id)).toEqual(["exec:manual"]);
  });

  // The level must not become a second place the same trading is counted.
  it("folds an agent out of its children's spines, once each", () => {
    const tree = buildTree(mixedFleet(), "All", { grouping: ["agent", "bot"] });
    // Two controllers at 12 apiece; the third belongs to no agent.
    expect(foldLeaves(node(tree, `agent:${RUN_KEY}`).leaves, identity, NOW).net).toBe(24);
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(36);
  });

  // A controller's own leaf already covers every executor it ever ran, so the
  // settle rule must not let the agent level double-count them.
  it("still lets a controller's leaf cover its executors", () => {
    const ctrl = leafFromController(
      controller({ bot_name: "brigado-brl_mm-btc", controller_id: "pmm_1" }),
      RUN_KEY,
    );
    const child = leafFromExecutor(
      executor({ id: "ex_1", controller_id: "pmm_1" }),
      "brigado-brl_mm-btc",
      RUN_KEY,
    );
    const tree = buildTree([ctrl, child], "All", { grouping: ["agent", "bot"] });
    expect(node(tree, `agent:${RUN_KEY}`).leaves.map((l) => l.id)).toEqual([ctrl.id]);
    expect(foldLeaves(node(tree, `agent:${RUN_KEY}`).leaves, identity, NOW).net).toBe(12);
  });

  it("changes nothing at all when the level is off", () => {
    const off = buildTree(mixedFleet(), "All", { grouping: ["bot"] });
    expect(off.children.map((c) => c.id)).toEqual(["bot:brigado-brl_mm-btc", "bot:hand-rolled"]);
    expect(countNodes(off, "agent")).toBe(0);
    expect(foldLeaves(off.leaves, identity, NOW).net).toBe(36);
  });

  it("nests controllers under the agent directly when the bot level is off", () => {
    const tree = buildTree(mixedFleet(), "All", { grouping: ["agent"] });
    expect(node(tree, `agent:${RUN_KEY}`).children.map((c) => c.id)).toEqual([
      "ctrl:brigado-brl_mm-btc:pmm_1",
      "ctrl:brigado-brl_mm-btc:pmm_2",
    ]);
  });

  it("counts agent rows and walks them before their bots", () => {
    const tree = buildTree(mixedFleet(), "All", { grouping: ["agent", "bot"] });
    // The named run and the bucket the unowned bot fell into: both are rows.
    expect(countNodes(tree, "agent")).toBe(2);
    expect(visibleNodeIds(tree, new Set(["all"]))).toEqual([
      "all",
      `agent:${RUN_KEY}`,
      `agent:${BEFORE_LEDGER}`,
    ]);
    expect(ancestorChain(tree, "ctrl:brigado-brl_mm-btc:pmm_1")).toEqual([
      "ctrl:brigado-brl_mm-btc:pmm_1",
      "bot:brigado-brl_mm-btc",
      `agent:${RUN_KEY}`,
      "all",
    ]);
  });

  it("reads the run key back off an agent node id", () => {
    expect(agentOfNodeId(`agent:${RUN_KEY}`)).toBe(RUN_KEY);
    expect(agentOfNodeId("bot:alpha")).toBeNull();
    expect(agentOfNodeId("all")).toBeNull();
  });

  // A filter that removes the bot should leave the reader on the agent that
  // operates it, not all the way back on the fleet.
  it("falls back from a lost controller to its agent when the bot is gone too", () => {
    const lost = mixedFleet()[0];
    const tree = indexTree(
      buildTree(
        [leafFromController(controller({ bot_name: "brigado-brl_mm-eth" }), RUN_KEY)],
        "All",
        { grouping: ["agent", "bot"] },
      ),
    );
    expect(resolveScope(tree, "ctrl:brigado-brl_mm-btc:pmm_1", lost)).toBe(`agent:${RUN_KEY}`);
  });

  // An agent scope is shallower than a bot, so it must never fall *into* one of
  // its own bots — that is a much narrower report than the link asked for.
  it("never resolves an agent scope down into one of its bots", () => {
    const other = indexTree(
      buildTree([leafFromController(controller({ bot_name: "beta" }))], "All", { grouping: ["agent", "bot"] }),
    );
    expect(resolveScope(other, `agent:${RUN_KEY}`)).toBe("all");
  });

  // The ids in existing links are the product; only their parent changed.
  it("leaves the ctrl: and exec: id grammars alone", () => {
    const tree = indexTree(
      buildTree(mixedFleet(), "All", { grouping: ["agent", "bot"] }),
    );
    expect(resolveScope(tree, "ctrl:brigado-brl_mm-btc:pmm_1")).toBe(
      "ctrl:brigado-brl_mm-btc:pmm_1",
    );
    expect(resolveScope(tree, "ctrl:hand-rolled:grid_1")).toBe("ctrl:hand-rolled:grid_1");
  });
});

describe("buildTree, grouping by pair and by class (FEAT-107)", () => {
  const BRIGADO = "brigado.brl_mm";

  /** Two owners, two pairs, two classes — every axis has something to say. */
  const fleet = (): PerfLeaf[] => [
    leafFromController(
      controller({ bot_name: "brigado-brl_mm-sol", controller_id: "pmm_1" }),
      BRIGADO,
    ),
    leafFromController(
      controller({
        bot_name: "brigado-brl_mm-btc",
        controller_id: "grid_1",
        controller_name: "grid_strike",
        trading_pair: "BTC-USDT",
      }),
      BRIGADO,
    ),
    leafFromController(
      controller({ bot_name: "hand-rolled", controller_id: "pmm_2", trading_pair: "BTC-USDT" }),
    ),
  ];

  it("puts each pair's records under a row of its own", () => {
    const tree = buildTree(fleet(), "All", { grouping: ["pair"] });
    expect(tree.children.map((c) => c.id)).toEqual(["pair:SOL-USDC", "pair:BTC-USDT"]);
    expect(node(tree, "pair:BTC-USDT").children.map((c) => c.id)).toEqual([
      "ctrl:brigado-brl_mm-btc:grid_1",
      "ctrl:hand-rolled:pmm_2",
    ]);
  });

  it("puts each controller class under a row of its own", () => {
    const tree = buildTree(fleet(), "All", { grouping: ["ctrlType"] });
    expect(tree.children.map((c) => c.id)).toEqual(["class:pmm_simple", "class:grid_strike"]);
    expect(node(tree, "class:pmm_simple").children.map((c) => c.id)).toEqual([
      "ctrl:brigado-brl_mm-sol:pmm_1",
      "ctrl:hand-rolled:pmm_2",
    ]);
  });

  // The whole point of the fold being one function: the nesting is a reading
  // order, and a reading order cannot change what anything adds up to.
  it("reports the same fleet total under every nesting", () => {
    const groupings: GroupAxis[][] = [[], ["agent", "bot"], ["bot"], ["pair"], ["ctrlType"]];
    const totals = groupings.map(
      (grouping) => foldLeaves(buildTree(fleet(), "All", { grouping }).leaves, identity, NOW).net,
    );
    expect(new Set(totals).size).toBe(1);
    expect(totals[0]).toBe(36);
  });

  // A pair is not unique the way a bot is — SOL-USDC is traded by everyone — so
  // a nested pair row carries its parent in its id. Without that, two owners'
  // SOL-USDC rows are one node filed under whichever record was read first,
  // which is one agent's money drawn inside another agent's subtree.
  it("keeps two owners' rows for the same pair apart", () => {
    const leaves = [
      leafFromController(controller({ bot_name: "brigado-brl_mm-sol" }), BRIGADO),
      leafFromController(
        controller({ bot_name: "vega-mom-sol", controller_id: "v1" }),
        "vega.mom",
      ),
    ];
    const tree = buildTree(leaves, "All", { grouping: ["agent", "pair"] });
    expect(node(tree, `agent:${BRIGADO}`).children.map((c) => c.id)).toEqual([
      `agent:${BRIGADO}>pair:SOL-USDC`,
    ]);
    expect(node(tree, "agent:vega.mom").children.map((c) => c.id)).toEqual([
      "agent:vega.mom>pair:SOL-USDC",
    ]);
    // Each holds only its own, which is what the qualified id buys.
    expect(
      node(tree, `agent:${BRIGADO}>pair:SOL-USDC`).leaves.map((leaf) => leaf.agent),
    ).toEqual([BRIGADO]);
  });

  // An executor carries no class of its own, so under `ctrlType` it can say
  // nothing about where its controller's row hangs — and the controller's own
  // record must decide, whichever of the two came off the wire first.
  it("places a controller's row from its own record, not from an executor read first", () => {
    const ctrl = leafFromController(controller({ controller_id: "pmm_1" }));
    const child = leafFromExecutor(executor({ id: "ex_1", controller_id: "pmm_1" }), "alpha");
    const executorFirst = buildTree([child, ctrl], "All", { grouping: ["ctrlType"] });
    const controllerFirst = buildTree([ctrl, child], "All", { grouping: ["ctrlType"] });
    for (const tree of [executorFirst, controllerFirst]) {
      expect(tree.children.map((c) => c.id)).toEqual(["class:pmm_simple"]);
      expect(node(tree, "class:pmm_simple").children.map((c) => c.id)).toEqual([
        "ctrl:alpha:pmm_1",
      ]);
    }
  });

  // A dash is the absence of a class rather than one, so it buys no row — the
  // same rule `controllerClassOf` states for the filter bubbles.
  it("skips the level for a controller whose class was never reported", () => {
    const unknown = leafFromController(
      controller({ controller_name: "", controller_type: "", controller_id: "x1" }),
    );
    const tree = buildTree([unknown, ...fleet()], "All", { grouping: ["ctrlType"] });
    expect(tree.children.map((c) => c.id)).toEqual([
      "ctrl:alpha:x1",
      "class:pmm_simple",
      "class:grid_strike",
    ]);
  });

  // A link written under one reading, opened under another: the level it names
  // is not in this tree, so it degrades to the report that contains it rather
  // than erroring or emptying the page.
  it("degrades a scope naming a level this nesting does not have", () => {
    const byPair = indexTree(buildTree(fleet(), "All", { grouping: ["pair"] }));
    const grouping: GroupAxis[] = ["pair"];
    expect(resolveScope(byPair, "bot:hand-rolled", undefined, { grouping })).toBe("all");
    expect(resolveScope(byPair, `agent:${BRIGADO}`, undefined, { grouping })).toBe("all");
    // ...and one it does have still lands on itself.
    expect(resolveScope(byPair, "pair:BTC-USDT", undefined, { grouping })).toBe("pair:BTC-USDT");
  });
});

describe("buildTree, the owner rows (FEAT-107)", () => {
  const BRIGADO = "brigado.brl_mm";
  const deeds: DeedIndex = { bots: {}, since: (NOW - 5 * HOUR) / 1000 };

  it("draws the two unowned buckets as rows, after the runs that were credited", () => {
    const leaves = [
      // Older than the ledger reaches: unjudgeable, not accused.
      leafFromController(
        controller({ controller_id: "old", deployed_at: new Date(NOW - 9 * HOUR).toISOString() }),
      ),
      // Inside the ledger's reach with no deed behind it: not Condor's.
      leafFromController(
        controller({
          bot_name: "stranger",
          controller_id: "new",
          deployed_at: new Date(NOW - HOUR).toISOString(),
        }),
      ),
      leafFromController(
        controller({ bot_name: "brigado-brl_mm-sol", controller_id: "b1" }),
        BRIGADO,
      ),
    ];
    const tree = buildTree(leaves, "All", { grouping: ["agent"], deeds });
    expect(tree.children.map((c) => c.id)).toEqual([
      `agent:${BRIGADO}`,
      `agent:${OUTSIDE}`,
      `agent:${BEFORE_LEDGER}`,
    ]);
    expect(node(tree, `agent:${OUTSIDE}`).children.map((c) => c.id)).toEqual(["ctrl:stranger:new"]);
    // Ordinary rows with ordinary folds: nothing is left out of the fleet.
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(36);
  });

  it("counts the buckets as owners for the collapse rule", () => {
    const oneBucket = [leafFromController(controller())];
    expect(buildTree(oneBucket, "All", { grouping: [], deeds }).children.map((c) => c.id)).toEqual([
      "ctrl:alpha:pmm_1",
    ]);
  });
});

describe("ancestorChain", () => {
  const tree = buildTree([
    leafFromController(controller()),
    leafFromExecutor(executor({ id: "a", status: "active", close_timestamp: 0 }), "alpha"),
  ]);

  it("walks from a node up to the fleet", () => {
    expect(ancestorChain(tree, "exec:a")).toEqual(["exec:a", "ctrl:alpha:pmm_1", "all"]);
  });

  it("is empty for a node that is not in the tree", () => {
    expect(ancestorChain(tree, "exec:gone")).toEqual([]);
  });

  it("names the nearest surviving ancestor after a population switch", () => {
    // The executor is gone from the next tree; its controller is not.
    const chain = ancestorChain(tree, "exec:a");
    const next = indexTree(buildTree([leafFromController(controller())]));
    expect(chain.find((id) => next.has(id))).toBe("ctrl:alpha:pmm_1");
  });
});

describe("resolveScope", () => {
  const running = indexTree(
    buildTree([
      leafFromController(controller()),
      leafFromExecutor(executor({ id: "a", status: "active", close_timestamp: 0 }), "alpha"),
    ]),
  );

  it("keeps a scope that is still there", () => {
    expect(resolveScope(running, "exec:a")).toBe("exec:a");
    expect(resolveScope(running, "ctrl:alpha:pmm_1")).toBe("ctrl:alpha:pmm_1");
    expect(resolveScope(running, "all")).toBe("all");
  });

  it("keeps an executor across a population switch, its id being the same in both", () => {
    // The point of an executor node id saying nothing about where it hangs: the
    // same executor is the same node whether it is live or archived.
    const terminated = indexTree(buildTree([leafFromExecutor(executor({ id: "a" }))]));
    expect(resolveScope(terminated, "exec:a")).toBe("exec:a");
  });

  it("falls back to the fleet when a controller is gone", () => {
    const withoutCtrl = indexTree(
      buildTree([leafFromController(controller({ controller_id: "other" }))]),
    );
    expect(resolveScope(withoutCtrl, "ctrl:alpha:pmm_1")).toBe("all");
  });

  it("uses a known leaf to re-aim an executor at its controller", () => {
    const leaf = leafFromExecutor(executor({ id: "a" }), "alpha");
    const withoutExec = indexTree(buildTree([leafFromController(controller())]));
    expect(resolveScope(withoutExec, "exec:a", leaf)).toBe("ctrl:alpha:pmm_1");
  });

  // A link written before the grouping level was retired names a node no tree
  // has any more. It has to land on the report it asked for rather than on an
  // empty screen, and the fleet is the only honest answer: `bot:alpha` meant
  // "everything alpha did", which the bot bubble now says instead.
  it("sends a retired group id to the fleet rather than into a controller", () => {
    const leaf = leafFromController(controller());
    const nodes = indexTree(buildTree([leaf]));
    expect(resolveScope(nodes, "bot:alpha", leaf)).toBe("all");
    expect(resolveScope(nodes, "type:pmm_simple", leaf)).toBe("all");
  });


  it("answers the fleet for something it cannot place at all", () => {
    expect(resolveScope(running, "nonsense")).toBe("all");
  });
});

describe("visibleNodeIds", () => {
  const tree = buildTree([
    leafFromController(controller()),
    leafFromExecutor(executor({ id: "a", status: "active", close_timestamp: 0 }), "alpha"),
  ]);

  it("lists every row in the order the tree draws them", () => {
    expect(visibleNodeIds(tree, new Set(["all", "ctrl:alpha:pmm_1"]))).toEqual([
      "all",
      "ctrl:alpha:pmm_1",
      "exec:a",
    ]);
  });

  // Shut is the default, so a fleet of a hundred and nineteen executors opens
  // as a list of controllers rather than as every executor it has ever run.
  it("draws only what has been opened, so the arrows walk what is on screen", () => {
    expect(visibleNodeIds(tree, new Set(["all"]))).toEqual(["all", "ctrl:alpha:pmm_1"]);
    expect(visibleNodeIds(tree, new Set())).toEqual(["all"]);
  });
});

// CORR-322: a bot nested under an agent row used to be marked open by
// `openRows`'s hardcoded `kind === "bot"` loop and drawn nowhere, because
// `visibleNodeIds` stops descending at the shut `agent:` row above it. These
// pin `autoOpenIds` — the extracted, named version of that loop — against the
// tree shape that actually breaks: a bot the agent operates.
describe("autoOpenIds", () => {
  const RUN_KEY = "brigado.brl_mm";

  it("opens an agent row far enough to reveal the bot nested under it, on first render", () => {
    const tree = buildTree(
      [leafFromController(controller({ bot_name: "brigado-brl_mm-btc" }), RUN_KEY)],
      "All",
      { grouping: ["agent", "bot"] },
    );
    const nodes = indexTree(tree);

    // Nothing selected, nothing hand-shut: the reader's very first render.
    const drawn = autoOpenIds(nodes, new Set());
    expect(drawn).toEqual(new Set([`agent:${RUN_KEY}`, "bot:brigado-brl_mm-btc"]));

    // What the reader actually sees: root open plus what the default opens.
    const open = new Set(["all", ...drawn]);
    expect(visibleNodeIds(tree, open)).toEqual([
      "all",
      `agent:${RUN_KEY}`,
      "bot:brigado-brl_mm-btc",
      "ctrl:brigado-brl_mm-btc:pmm_1",
    ]);
  });

  it("keeps opening a bot parented directly by the fleet, exactly as before", () => {
    const tree = buildTree([leafFromController(controller({ bot_name: "hand-rolled" }))], "All", { grouping: ["bot"] });
    expect(autoOpenIds(indexTree(tree), new Set())).toEqual(new Set(["bot:hand-rolled"]));
  });

  it("leaves the unattached row and its group shut — ARCH-318, not an oversight", () => {
    const manual = leafFromExecutor(executor({ id: "manual", controller_id: "main" }));
    const tree = buildTree([manual], "All", { grouping: ["agent", "bot"] });
    const bucket = `agent:${BEFORE_LEDGER}`;
    expect(node(tree, `${bucket}>grp:main`).kind).toBe("group");
    expect(node(tree, `${bucket}>orphans`).kind).toBe("orphans");
    expect(AUTO_OPEN_KINDS.has("group")).toBe(false);
    expect(AUTO_OPEN_KINDS.has("orphans")).toBe(false);
    const drawn = autoOpenIds(indexTree(tree), new Set());
    expect(drawn).not.toContain(`${bucket}>grp:main`);
    expect(drawn).not.toContain(`${bucket}>orphans`);
  });

  it("still honours a row the reader has shut by hand", () => {
    const tree = buildTree(
      [leafFromController(controller({ bot_name: "brigado-brl_mm-btc" }), RUN_KEY)],
      "All",
      { grouping: ["agent", "bot"] },
    );
    const nodes = indexTree(tree);
    expect(autoOpenIds(nodes, new Set([`agent:${RUN_KEY}`]))).toEqual(
      new Set(["bot:brigado-brl_mm-btc"]),
    );
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
      const ctrl = node(buildTree(leaves), "ctrl:alpha:pmm_1");
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

/**
 * What the sidebar's "Controller type" row may offer.
 *
 * The row is built by tallying this over the population and dropping empty
 * keys, so "belongs to no class" and "is not offered as a class" are the same
 * fact — which is what stops the row repeating the executor-type row below it,
 * and what stops it offering a bubble for every record whose class upstream
 * never reported.
 */
describe("controllerClassOf", () => {
  const classes = new Map([["pmm_1", "pmm_simple"]]);

  it("classes a controller as itself", () => {
    const leaf = leafFromController(controller({ controller_name: "grid_strike" }));
    expect(controllerClassOf(leaf, classes)).toBe("grid_strike");
  });

  it("classes an executor by the controller that ran it, never by its own type", () => {
    const leaf = leafFromExecutor(executor({ type: "grid_executor", controller_id: "pmm_1" }), "alpha");
    expect(controllerClassOf(leaf, classes)).toBe("pmm_simple");
  });

  it("gives a controller-less executor no class, so it cannot repeat the executor-type row", () => {
    const leaf = leafFromExecutor(executor({ type: "grid_executor", controller_id: "" }));
    expect(controllerClassOf(leaf, classes)).toBe("");
  });

  it("gives an executor whose controller has no reported class no class either", () => {
    const leaf = leafFromExecutor(executor({ controller_id: "unknown_ctrl" }), "alpha");
    expect(controllerClassOf(leaf, classes)).toBe("");
  });

  it("never offers the unknown dash as a class, however a terminated record arrives", () => {
    const leaf = leafFromTerminatedController(
      { ...controller({ controller_name: "" }), status: "stopped" },
      botRun({ bot_name: "alpha" }),
    );
    expect(leaf.executorType).toBe("—");
    expect(controllerClassOf(leaf, classes)).toBe("");
  });
});

/**
 * The granularity switch (ARCH-317), which is one predicate over `leaf.kind`
 * and then `buildTree`'s spine rule.
 *
 * Both non-default modes are a way to report a number nobody earned, which is
 * what these pin: `Controllers` must leave every controller's own total exactly
 * where `Both` had it (a controller's spine is its own record, so dropping its
 * children cannot change it), and `Executors` must fold each controller row out
 * of the executors under it *once* — never the controller record and its
 * executors added together.
 */
describe("matchesGrain, and the trees it builds", () => {
  /**
   * A fleet with all three things in it: a controller with executors under it,
   * and two hand-opened positions that belong to no controller at all — which
   * is the `grp:` bucket the level before this one introduced.
   */
  const fleet = (): PerfLeaf[] => [
    leafFromController(controller({ bot_name: "alpha", controller_id: "pmm_1" })),
    leafFromExecutor(executor({ id: "e1", pnl: 3, controller_id: "pmm_1" }), "alpha"),
    leafFromExecutor(executor({ id: "e2", pnl: 4, controller_id: "pmm_1" }), "alpha"),
    leafFromExecutor(executor({ id: "manual", pnl: 5, controller_id: "main" })),
    leafFromExecutor(executor({ id: "manual_2", pnl: 7, controller_id: "main" })),
  ];

  const at = (grain: Grain) =>
    buildTree(fleet().filter((leaf) => matchesGrain(leaf, grain)), "All", { grouping: ["bot"] });

  const netOf = (tree: PerfNode, id: string) =>
    foldLeaves(node(tree, id).leaves, identity, NOW).net;

  it("keeps every leaf at the default, which is the tree as it was", () => {
    expect(fleet().every((leaf) => matchesGrain(leaf, "both"))).toBe(true);
  });

  it("draws no executor row anywhere in controllers mode", () => {
    const tree = at("controllers");
    expect(countNodes(tree, "executor")).toBe(0);
    expect(node(tree, "ctrl:alpha:pmm_1").children).toEqual([]);
  });

  // The point of the mode: a controller's spine is its own record, so hiding
  // the executors underneath it must not move a single number.
  it("reports the same controller totals in controllers mode as in both", () => {
    expect(netOf(at("controllers"), "ctrl:alpha:pmm_1")).toBe(
      netOf(at("both"), "ctrl:alpha:pmm_1"),
    );
    expect(netOf(at("controllers"), "bot:alpha")).toBe(netOf(at("both"), "bot:alpha"));
  });

  // The bucket exists only to hold executors, so a mode with no executors in it
  // must not leave the row standing as an empty husk — and it does not, because
  // nothing builds a node the leaves never ask for.
  it("leaves no empty group row behind in controllers mode", () => {
    const tree = at("controllers");
    expect(countNodes(tree, "group")).toBe(0);
    expect(tree.children.map((c) => c.id)).toEqual(["bot:alpha"]);
    expect(indexTree(tree).has("grp:main")).toBe(false);
  });

  it("draws no controller record in executors mode, only rows folded from executors", () => {
    const tree = at("executors");
    // The controller row survives — as a fold of its executors, which is
    // exactly what the executor-type filter has always produced.
    const ctrl = node(tree, "ctrl:alpha:pmm_1");
    expect(ctrl.leaves.every((leaf) => leaf.kind === "executor")).toBe(true);
    expect(ctrl.children.map((c) => c.id)).toEqual(["exec:e1", "exec:e2"]);
    expect(foldLeaves(ctrl.leaves, identity, NOW).net).toBe(7);
  });

  // The bucket is untouched by the mode: the rows in it were executors already.
  it("keeps the group row and its executors in executors mode", () => {
    const tree = at("executors");
    const group = node(tree, "grp:main");
    expect(group.children.map((c) => c.id)).toEqual(["exec:manual", "exec:manual_2"]);
    expect(foldLeaves(group.leaves, identity, NOW).net).toBe(12);
  });

  // No double counting, said as the sum: the fleet total is the executor leaves
  // and nothing else, so the controller record cannot have been added on top.
  it("folds the fleet out of the executor leaves alone in executors mode", () => {
    const tree = at("executors");
    expect(tree.leaves.every((leaf) => leaf.kind === "executor")).toBe(true);
    expect(tree.leaves.length).toBe(4);
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(3 + 4 + 5 + 7);
  });

  // ...and the same on the other side: the fleet is the controller record and
  // nothing else, never the controller's executors counted alongside it.
  it("folds the fleet out of the controller leaves alone in controllers mode", () => {
    const tree = at("controllers");
    expect(tree.leaves.every((leaf) => leaf.kind === "controller")).toBe(true);
    expect(foldLeaves(tree.leaves, identity, NOW).net).toBe(
      foldLeaves([leafFromController(controller())], identity, NOW).net,
    );
  });
});
