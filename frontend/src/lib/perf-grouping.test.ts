/**
 * The order the fleet is read in (FEAT-107).
 *
 * Three questions, and the third is the one with teeth. What a link says
 * (`parseGrouping`), what is worth drawing (`collapseGrouping`), and — the rule
 * FEAT-108 left behind — **the browser always draws the level its root lives
 * on**, or a rooted host reports an empty fleet and says nothing about why.
 */

import { describe, expect, it } from "vitest";

import { BEFORE_LEDGER, OUTSIDE } from "@/components/perf/agentFilter";
import type { DeedIndex } from "@/lib/agent-attribution";
import type { ControllerInfo } from "@/lib/api";
import {
  collapseGrouping,
  distinguishes,
  DEFAULT_GROUPING,
  formatGrouping,
  groupingForRoot,
  GROUPING_PRESETS,
  parseGrouping,
  presetOf,
  rootAxis,
  type GroupAxis,
} from "@/lib/perf-grouping";
import { buildTree, indexTree, leafFromController, type PerfLeaf } from "@/lib/perf-tree";

const controller = (over: Partial<ControllerInfo> = {}): ControllerInfo =>
  ({
    controller_name: "pmm_simple",
    controller_type: "market_making",
    controller_id: "pmm_1",
    bot_name: "alpha",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 10,
    unrealized_pnl_quote: 2,
    global_pnl_quote: 12,
    global_pnl_pct: 1,
    volume_traded: 500,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: "2026-09-01T08:00:00Z",
    config: {},
    ...over,
  }) as ControllerInfo;

const BRIGADO = "brigado.brl_mm";

describe("parseGrouping / formatGrouping", () => {
  it("reads the order a link carries", () => {
    expect(parseGrouping("agent.bot")).toEqual(["agent", "bot"]);
    expect(parseGrouping("pair")).toEqual(["pair"]);
    expect(parseGrouping("pair.agent.bot")).toEqual(["pair", "agent", "bot"]);
  });

  it("lands on the default for an absent, empty or unreadable parameter", () => {
    expect(parseGrouping(null)).toEqual([...DEFAULT_GROUPING]);
    expect(parseGrouping("")).toEqual([...DEFAULT_GROUPING]);
    // A hand-edited parameter, and the retired `type:` grouping's own word.
    expect(parseGrouping("nonsense")).toEqual([...DEFAULT_GROUPING]);
    expect(parseGrouping("type")).toEqual([...DEFAULT_GROUPING]);
  });

  it("drops what it cannot read and keeps what it can", () => {
    expect(parseGrouping("pair.nonsense")).toEqual(["pair"]);
    expect(parseGrouping("pair.pair.bot")).toEqual(["pair", "bot"]);
  });

  it("round-trips, including the fleet read with no levels at all", () => {
    for (const preset of GROUPING_PRESETS) {
      expect(parseGrouping(formatGrouping(preset.axes))).toEqual([...preset.axes]);
    }
    expect(parseGrouping(formatGrouping([]))).toEqual([]);
  });

  it("names every preset and nothing else", () => {
    expect(presetOf(["agent", "bot"])?.key).toBe("owner");
    expect(presetOf(["pair"])?.key).toBe("pair");
    expect(presetOf(["pair", "agent"])).toBeNull();
  });
});

describe("collapseGrouping", () => {
  const owned = leafFromController(controller({ bot_name: "brigado-brl_mm-btc" }), BRIGADO);
  const other = leafFromController(controller({ bot_name: "hand-rolled", trading_pair: "BTC-USDT" }));

  it("drops a nested level that tells nothing apart — a one-bot owner spends no chevron", () => {
    const one = [leafFromController(controller())];
    expect(collapseGrouping(["agent", "bot"], one, null)).toEqual(["agent"]);
  });

  // The correction the eighteen-controller fleet forced: pressing Pair on a
  // fleet that trades one pair answered with eighteen bare controller rows and
  // no total anywhere. The outermost axis is the question that was asked.
  it("never drops the outermost axis, whatever the population agrees about", () => {
    const one = [leafFromController(controller())];
    expect(collapseGrouping(["pair"], one, null)).toEqual(["pair"]);
    expect(collapseGrouping(["ctrlType"], one, null)).toEqual(["ctrlType"]);
    expect(collapseGrouping(["bot"], one, null)).toEqual(["bot"]);
  });

  it("keeps a level the moment two records disagree", () => {
    expect(collapseGrouping(["agent", "bot"], [owned, other], null)).toEqual(["agent", "bot"]);
  });

  // FEAT-106 is what makes owner-first pay off: before it, a fleet where
  // nothing was attributed had one key on this axis and the level collapsed
  // entirely. Its two buckets tell records apart on almost every install.
  it("tells the two unowned buckets apart, which a bare run key could not", () => {
    const deeds: DeedIndex = { bots: {}, since: Date.parse("2026-09-01T00:00:00Z") / 1000 };
    const before = leafFromController(controller({ deployed_at: "2026-08-01T08:00:00Z" }));
    const after = leafFromController(controller({ deployed_at: "2026-09-02T08:00:00Z" }));
    expect(distinguishes([before, after], "agent", deeds)).toBe(true);
    expect(distinguishes([before, after], "agent", null)).toBe(false);
    expect(collapseGrouping(["agent"], [before, after], deeds)).toEqual(["agent"]);
  });

  it("never drops the axis it is told to keep, wherever it is nested", () => {
    const one = [leafFromController(controller({ bot_name: "brigado-brl_mm-btc" }), BRIGADO)];
    expect(collapseGrouping(["agent", "bot"], one, null)).toEqual(["agent"]);
    expect(collapseGrouping(["agent", "bot"], one, null, "bot")).toEqual(["agent", "bot"]);
  });

  it("counts an empty population as distinguishing, so a filter does not reshape the tree", () => {
    expect(collapseGrouping(["agent", "bot"], [], null)).toEqual(["agent", "bot"]);
  });
});

describe("groupingForRoot — the browser always draws the level its root lives on", () => {
  it("leaves an unrooted browser's order alone", () => {
    expect(rootAxis("all")).toBeNull();
    expect(groupingForRoot(["pair"], "all")).toEqual(["pair"]);
  });

  it("forces the root's own axis in, whatever the reader asked for", () => {
    expect(groupingForRoot(["pair"], `agent:${BRIGADO}`)).toEqual(["agent", "pair"]);
    expect(groupingForRoot(["ctrlType"], "bot:alpha")).toEqual(["bot", "ctrlType"]);
  });

  // Not merely present but *first*, and this is the reason: a level nested
  // under another is drawn once per parent, so an agent root under `pair` would
  // be one node per pair — several rows for one floor, of which the sidebar
  // could draw only one.
  it("puts it first, so the root is one node and not one per parent", () => {
    expect(groupingForRoot(["pair", "agent"], `agent:${BRIGADO}`)).toEqual(["agent", "pair"]);
    expect(groupingForRoot(["agent", "bot"], `agent:${BRIGADO}`)).toEqual(["agent", "bot"]);
  });

  // The end the rule exists for, said against a real tree: whatever the reader
  // picks, the rooted host's floor has a node to be.
  it("leaves the root a node to be under every preset", () => {
    const leaves: PerfLeaf[] = [
      leafFromController(controller({ bot_name: "brigado-brl_mm-btc" }), BRIGADO),
      leafFromController(controller({ bot_name: "vega-mom-eth", controller_id: "v1" }), "vega.mom"),
    ];
    const root = `agent:${BRIGADO}`;
    for (const preset of GROUPING_PRESETS) {
      const grouping = collapseGrouping(
        groupingForRoot(preset.axes, root),
        leaves,
        null,
        rootAxis(root),
      );
      const nodes = indexTree(buildTree(leaves, "All", { grouping, deeds: null }));
      expect(nodes.has(root), `root missing under ${preset.key}`).toBe(true);
      // And it holds only its own: the floor is a floor under every reading.
      expect(nodes.get(root)!.leaves.map((leaf) => leaf.agent)).toEqual([BRIGADO]);
    }
  });

  // A fleet that is entirely one agent's would collapse the owner level away,
  // which is exactly when a rooted host would find its floor missing.
  it("survives a fleet with a single owner, where the level would otherwise collapse", () => {
    const leaves = [leafFromController(controller({ bot_name: "brigado-brl_mm-btc" }), BRIGADO)];
    const root = `agent:${BRIGADO}`;
    const grouping = collapseGrouping(
      groupingForRoot([...DEFAULT_GROUPING], root),
      leaves,
      null,
      rootAxis(root),
    );
    expect(grouping).toEqual(["agent"]);
    expect(indexTree(buildTree(leaves, "All", { grouping })).has(root)).toBe(true);
  });
});

describe("the axis vocabulary", () => {
  it("offers exactly the four axes the presets are built from", () => {
    const used = new Set<GroupAxis>(GROUPING_PRESETS.flatMap((preset) => [...preset.axes]));
    expect([...used].sort()).toEqual(["agent", "bot", "ctrlType", "pair"]);
  });

  it("reads the two unowned buckets as owner keys, not as an absence", () => {
    expect(parseGrouping("agent")).toEqual(["agent"]);
    expect([OUTSIDE, BEFORE_LEDGER].every((value) => value.startsWith(" "))).toBe(true);
  });
});
