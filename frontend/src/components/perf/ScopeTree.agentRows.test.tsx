/**
 * What an owner row in the scope picker calls itself (READ-364).
 *
 * Three kinds of row share the agent level and they are named by three
 * different rules, all of which live in `agentBucketLabel`: a real strategy
 * keeps its slugs, because the bot names beneath it are built out of exactly
 * those two slugs and that is what a reader matches by eye down the column; a
 * pseudo-run has no such bot name — it is built with an empty namespace on
 * purpose — so its slugs prove nothing and it says the words the fleet map
 * already ships; and the two rows that are not runs at all say what they are.
 *
 * Pinned at the rendered row rather than at the function, because the bug this
 * closes was a *row* reading `condor / ui` while the same key's tooltip two
 * files away already read `Condor / Dashboard`.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ScopeTree } from "./ScopeTree";
import type { FleetOwner } from "@/lib/agent-attribution";
import type { ControllerInfo } from "@/lib/api";
import { buildTree, leafFromController, type PerfLeaf } from "@/lib/perf-tree";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const controller = (bot: string, id: string): ControllerInfo =>
  ({
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: id,
    bot_name: bot,
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 10,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 10,
    global_pnl_pct: 2,
    volume_traded: 100,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: "2026-09-01T08:00:00Z",
    config: {},
  }) as ControllerInfo;

const owned = (bot: string, id: string, runKey: string): PerfLeaf =>
  leafFromController(controller(bot, id), runKey, runKey ? "deed" : "none");

/**
 * The map as `_pseudo_owners` builds it: the door's namespace is empty, and
 * `PSEUDO_STRATEGY_NAMES`' word arrives in `strategyName`. No copy of those
 * words exists in the browser, which is the point of reading them off here.
 */
const OWNERS: FleetOwner[] = [
  {
    runKey: "brigado.brl_mm",
    agentSlug: "brigado",
    agentName: "Brigado",
    strategySlug: "brl_mm",
    strategyName: "BRL MM",
    namespace: "brigado-brl_mm",
    declaredBots: [],
    agentIds: [],
    live: null,
  },
  ...(
    [
      ["ui", "Dashboard"],
      ["chat", "Chat"],
      ["delegation", "Delegation"],
    ] as const
  ).map(([slug, name]) => ({
    runKey: `condor.${slug}`,
    agentSlug: "condor",
    agentName: "Condor",
    strategySlug: slug,
    strategyName: name,
    namespace: "",
    declaredBots: [],
    agentIds: [],
    live: null,
  })),
];

const leaves = [
  owned("brigado-brl_mm-btc", "pmm_1", "brigado.brl_mm"),
  owned("sol_scalper", "pmm_2", "condor.ui"),
  owned("btc_grid", "pmm_3", "condor.chat"),
  owned("eth_dca", "pmm_4", "condor.delegation"),
];

let container: HTMLDivElement;
let root: Root;

/** Every row's visible name, in the order the picker draws them. */
const rowNames = () =>
  [...container.querySelectorAll<HTMLElement>("span")]
    .filter((el) => el.className.includes("text-[11px]") && !el.className.includes("tabular-nums"))
    .map((el) => el.textContent ?? "");

function draw(owners: FleetOwner[]) {
  const tree = buildTree(leaves, "All", { grouping: ["agent"] });
  act(() => {
    root.render(
      <ScopeTree
        root={tree}
        activeId="all"
        open={new Set<string>()}
        owners={owners}
        onSelect={() => {}}
        onToggleOpen={() => {}}
        cv={(v) => v}
        currencySymbol="$"
        now={Date.parse("2026-09-01T12:00:00Z")}
      />,
    );
  });
  return tree;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("ScopeTree owner rows", () => {
  it("names each of the three doors with the map's word, and a strategy with its slugs", () => {
    draw(OWNERS);
    // In the tree's own order, which is a fact about the records rather than
    // about what the rows are called — so renaming a row cannot reorder the
    // sidebar under the reader.
    expect(rowNames()).toEqual([
      "brigado / brl_mm",
      "Condor / Dashboard",
      "Condor / Chat",
      "Condor / Delegation",
    ]);
  });

  it("leaves the row ids — and so the scope URL — untouched", () => {
    // Only the displayed text changes: `condor.chat`, `condor.ui` and
    // `condor.delegation` stay three distinct keys and three distinct rows,
    // because "the chat deployed it" and "somebody pressed Deploy" are
    // different answers.
    const tree = draw(OWNERS);
    expect(tree.children.map((node) => node.id)).toEqual([
      "agent:brigado.brl_mm",
      "agent:condor.ui",
      "agent:condor.chat",
      "agent:condor.delegation",
    ]);
  });

  it("falls back to slugs for a door the map no longer holds", () => {
    // A stale deep link names something rather than nothing.
    draw([]);
    expect(rowNames()).toEqual([
      "brigado / brl_mm",
      "condor / ui",
      "condor / chat",
      "condor / delegation",
    ]);
  });
});
