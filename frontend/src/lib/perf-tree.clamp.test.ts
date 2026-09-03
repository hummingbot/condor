/**
 * The floor, pinned (FEAT-108).
 *
 * The fleet browser can be rooted at something narrower than the fleet — the
 * agent workspace roots it at the one agent whose screen it is — and the whole
 * correctness argument of that feature is that the root is a **clamp** and not
 * a default. A default is escapable: a click on a sibling row, a stale link, a
 * population switch, a filter that removed the root's last leaf. Each of those
 * would put another agent's money on screen under this agent's name, so each
 * of them is a case below.
 */

import { describe, expect, it } from "vitest";

import type { ControllerInfo } from "./api";
import {
  buildTree,
  clampScope,
  emptyScopeNode,
  indexTree,
  leafFromController,
  resolveScope,
} from "./perf-tree";

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
    volume_traded: 100,
    deployed_at: "2026-09-01T10:00:00Z",
    ...over,
  } as ControllerInfo;
}

const BRIGADO = "brigado.brl_mm";
const OTHER = "vega.momentum";

/** A fleet two agents are trading on, grouped by both levels. */
const tree = buildTree(
  [
    leafFromController(
      controller({ bot_name: "brigado-brl_mm-btc", controller_id: "b1" }),
      BRIGADO,
      "namespace",
    ),
    leafFromController(
      controller({ bot_name: "vega-momentum-eth", controller_id: "v1" }),
      OTHER,
      "namespace",
    ),
  ],
  "All",
  { groupByBot: true, groupByAgent: true },
);
const nodes = indexTree(tree);

describe("clampScope", () => {
  it("leaves every scope alone when the root is the fleet", () => {
    for (const id of nodes.keys()) {
      expect(clampScope(tree, id, "all")).toBe(id);
    }
  });

  it("keeps a scope inside the root's subtree", () => {
    expect(clampScope(tree, `agent:${BRIGADO}`, `agent:${BRIGADO}`)).toBe(`agent:${BRIGADO}`);
    expect(clampScope(tree, "bot:brigado-brl_mm-btc", `agent:${BRIGADO}`)).toBe(
      "bot:brigado-brl_mm-btc",
    );
    expect(clampScope(tree, "ctrl:brigado-brl_mm-btc:b1", `agent:${BRIGADO}`)).toBe(
      "ctrl:brigado-brl_mm-btc:b1",
    );
  });

  // The escape the whole feature is exposed to: one click on a row that is on
  // screen but belongs to somebody else.
  it("pulls a sibling agent's scope back to the root", () => {
    expect(clampScope(tree, `agent:${OTHER}`, `agent:${BRIGADO}`)).toBe(`agent:${BRIGADO}`);
    expect(clampScope(tree, "bot:vega-momentum-eth", `agent:${BRIGADO}`)).toBe(
      `agent:${BRIGADO}`,
    );
    expect(clampScope(tree, "ctrl:vega-momentum-eth:v1", `agent:${BRIGADO}`)).toBe(
      `agent:${BRIGADO}`,
    );
  });

  it("pulls the fleet itself back to the root — a floor is not a starting point", () => {
    expect(clampScope(tree, "all", `agent:${BRIGADO}`)).toBe(`agent:${BRIGADO}`);
  });

  it("pulls an id that is in no tree back to the root", () => {
    expect(clampScope(tree, "nonsense", `agent:${BRIGADO}`)).toBe(`agent:${BRIGADO}`);
  });

  // The two rules compose in the order the browser applies them: the fallback
  // first (a scope whose node has gone), then the floor — because a fallback is
  // one of the ways a scope ends up outside the root.
  it("catches a fallback that escaped the root", () => {
    const gone = "ctrl:vega-momentum-eth:v1";
    const aimed = resolveScope(indexTree(buildTree([])), gone);
    expect(aimed).toBe("all");
    expect(clampScope(tree, aimed, `agent:${BRIGADO}`)).toBe(`agent:${BRIGADO}`);
  });

  // Containment is read off the tree that was actually built, not off the id
  // grammar — which is what keeps the clamp honest under a tree grouped some
  // other way (FEAT-107 owns the grouping axes).
  it("reads containment from the tree, not from the id prefix", () => {
    const flat = buildTree(
      [
        leafFromController(
          controller({ bot_name: "brigado-brl_mm-btc", controller_id: "b1" }),
          BRIGADO,
          "namespace",
        ),
      ],
      "All",
      { groupByBot: false, groupByAgent: false },
    );
    // `bot:` sits under `agent:` by the id grammar, but this tree has neither
    // level, so the controller is not inside the agent — and the clamp says so.
    expect(clampScope(flat, "ctrl:brigado-brl_mm-btc:b1", `agent:${BRIGADO}`)).toBe(
      `agent:${BRIGADO}`,
    );
  });
});

describe("emptyScopeNode", () => {
  it("reports an empty scope of the right kind rather than the fleet", () => {
    const node = emptyScopeNode(`agent:${BRIGADO}`, "Brigado · brl_mm");
    expect(node).toEqual({
      id: `agent:${BRIGADO}`,
      kind: "agent",
      label: "Brigado · brl_mm",
      leaves: [],
      children: [],
    });
  });

  it("reads every kind off the id grammar", () => {
    expect(emptyScopeNode("bot:alpha", "alpha").kind).toBe("bot");
    expect(emptyScopeNode("grp:main", "main").kind).toBe("group");
    expect(emptyScopeNode("ctrl:alpha:pmm_1", "pmm_1").kind).toBe("controller");
    expect(emptyScopeNode("exec:e1", "e1").kind).toBe("executor");
    expect(emptyScopeNode("orphans", "Unattached").kind).toBe("orphans");
    expect(emptyScopeNode("all", "All").kind).toBe("fleet");
  });
});
