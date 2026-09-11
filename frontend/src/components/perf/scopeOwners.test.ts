/**
 * The split follows the tree, not a special case (FEAT-116).
 *
 * The floor's chart was one line per agent over the whole fleet. What replaced
 * it is one line per **child of the scope you are on**, so the properties worth
 * pinning are the ones that make that a rule rather than a re-spelling of the
 * page it deletes: the same helper answers at the root, inside an agent and
 * under a different grouping; it refuses the three scopes where a split would
 * be a picture drawn twice; and the keys it hands the chart are the ones whose
 * union is the scope's own accounting spine — which is what makes the owner
 * lines add up to the Total.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { ownerNoun, scopeOwners } from "@/components/perf/scopeOwners";
import type { ControllerInfo } from "@/lib/api";
import {
  buildTree,
  type ConvertQuote,
  type GroupAxis,
  type PerfLeaf,
  type PerfNode,
} from "@/lib/perf-tree";

const NOW = Date.parse("2026-09-04T12:00:00Z");
const cv: ConvertQuote = (value) => value;
/** A ledger that opened two hours ago, so `agentBucket` can judge an unowned leaf. */
const DEEDS = { bots: {}, since: (NOW - 7_200_000) / 1000 } as never;

let seq = 0;

/** One live controller leaf, in the shape `runningLeaves` produces. */
function leaf(over: Partial<PerfLeaf> = {}): PerfLeaf {
  seq += 1;
  const bot = over.bot ?? `bot-${seq}`;
  const controllerId = over.controllerId ?? `c${seq}`;
  return {
    id: `${bot}:${controllerId}`,
    kind: "controller",
    label: controllerId,
    bot,
    agent: "",
    how: "none",
    controllerId,
    executorType: "pmm_simple",
    connector: "binance",
    pair: "SOL-USDC",
    realized: 0,
    unrealized: 0,
    net: 0,
    volume: 0,
    fees: 0,
    capital: 0,
    closeTypes: {},
    positions: [],
    startedAt: NOW - 3_600_000,
    endedAt: null,
    running: true,
    status: "running",
    source: {} as ControllerInfo,
    ...over,
  };
}

function tree(leaves: PerfLeaf[], grouping: readonly GroupAxis[] = ["agent", "bot"]) {
  return buildTree(leaves, "All", { grouping, deeds: DEEDS });
}

/** The node at `id`, anywhere under `root`. */
function nodeAt(root: PerfNode, id: string): PerfNode {
  if (root.id === id) return root;
  for (const child of root.children) {
    const found = nodeAt2(child, id);
    if (found) return found;
  }
  throw new Error(`no node ${id}`);
}

function nodeAt2(node: PerfNode, id: string): PerfNode | null {
  if (node.id === id) return node;
  for (const child of node.children) {
    const deeper = nodeAt2(child, id);
    if (deeper) return deeper;
  }
  return null;
}

beforeEach(() => {
  seq = 0;
});

describe("the fleet scope under the default grouping", () => {
  const leaves = [
    leaf({ agent: "alpha.mm", how: "namespace", bot: "alpha-mm-1", capital: 1_000 }),
    leaf({ agent: "beta.grid", how: "namespace", bot: "beta-grid-1", capital: 400 }),
  ];

  it("is the floor: one line per agent, plus the capital Relative divides by", () => {
    const owners = scopeOwners(tree(leaves), cv, NOW);
    expect(owners.map((owner) => owner.key)).toEqual(["agent:alpha.mm", "agent:beta.grid"]);
    expect(owners.map((owner) => owner.capital)).toEqual([1_000, 400]);
  });

  it("hands over keys whose union is the scope's own spine", () => {
    const root = tree(leaves);
    const owners = scopeOwners(root, cv, NOW);
    const union = new Set(owners.flatMap((owner) => owner.keys));
    // The invariant the Total line rests on: `ownerSeries` folds the Total over
    // exactly this union, so it is the scope's own series and not a subset.
    expect([...union].sort()).toEqual(
      root.leaves.filter((l) => l.kind === "controller").map((l) => l.id).sort(),
    );
  });
});

describe("one level in, and under another grouping", () => {
  const leaves = [
    leaf({ agent: "alpha.mm", how: "namespace", bot: "alpha-mm-1", pair: "SOL-USDC" }),
    leaf({ agent: "alpha.mm", how: "namespace", bot: "alpha-mm-2", pair: "BTC-USDT" }),
    leaf({ agent: "beta.grid", how: "namespace", bot: "beta-grid-1", pair: "SOL-USDC" }),
  ];

  it("draws one line per bot inside an agent", () => {
    const owners = scopeOwners(nodeAt(tree(leaves), "agent:alpha.mm"), cv, NOW);
    expect(owners.map((owner) => owner.key)).toEqual([
      "bot:alpha-mm-1",
      "bot:alpha-mm-2",
    ]);
  });

  it("draws one line per pair when the tree is grouped by pair", () => {
    const owners = scopeOwners(tree(leaves, ["pair"]), cv, NOW);
    expect(owners.map((owner) => owner.label).sort()).toEqual(["BTC-USDT", "SOL-USDC"]);
  });
});

describe("the three scopes that do not split", () => {
  it("refuses a scope with a single child — one line beside its equal is one picture", () => {
    const owners = scopeOwners(
      tree([leaf({ agent: "alpha.mm", how: "namespace", bot: "alpha-mm-1" })]),
      cv,
      NOW,
    );
    expect(owners).toEqual([]);
  });

  it("refuses a controller scope, whose children carry no history to draw", () => {
    const root = tree([
      leaf({ bot: "alpha-mm-1", controllerId: "c1", agent: "alpha.mm", how: "namespace" }),
      leaf({
        kind: "executor",
        id: "e1",
        bot: "alpha-mm-1",
        controllerId: "c1",
        agent: "alpha.mm",
        how: "namespace",
      }),
      leaf({
        kind: "executor",
        id: "e2",
        bot: "alpha-mm-1",
        controllerId: "c1",
        agent: "alpha.mm",
        how: "namespace",
      }),
    ]);
    expect(scopeOwners(nodeAt(root, "ctrl:alpha-mm-1:c1"), cv, NOW)).toEqual([]);
  });

  it("leaves out a child with no controller in its spine rather than listing a line it cannot draw", () => {
    const owners = scopeOwners(
      tree([
        leaf({ agent: "alpha.mm", how: "namespace", bot: "alpha-mm-1" }),
        leaf({ agent: "beta.grid", how: "namespace", bot: "beta-grid-1" }),
        // An agent whose whole spine is a standalone executor: its money is in
        // the fold and its line would be a legend entry that never draws.
        leaf({
          kind: "executor",
          id: "e9",
          bot: "gamma-1",
          controllerId: "",
          agent: "gamma.hand",
          how: "namespace",
        }),
      ]),
      cv,
      NOW,
    );
    expect(owners.map((owner) => owner.key)).toEqual([
      "agent:alpha.mm",
      "agent:beta.grid",
    ]);
  });
});

describe("what a line is called", () => {
  it("names the level rather than the node kind's spelling", () => {
    expect(ownerNoun("agent")).toBe("agent");
    expect(ownerNoun("bot")).toBe("bot");
    expect(ownerNoun("pair")).toBe("pair");
    expect(ownerNoun("ctrlType")).toBe("controller type");
  });

  it("says nothing false about a level with no noun of its own", () => {
    expect(ownerNoun("orphans")).toBe("part");
    expect(ownerNoun("group")).toBe("part");
  });
});
