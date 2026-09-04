/**
 * The relationship between the two numbers, pinned (FEAT-109).
 *
 * The feature is not "show two numbers" — a reader given two numbers with no
 * stated relationship assumes one of them is broken. It is the relationship,
 * and a relationship that is not tested is a sentence in a docstring. So every
 * case here is a way the reconciliation could quietly stop being true: the
 * headline drifting from what `/bots` shows at the same scope, a chat-deployed
 * bot being swallowed by the residual instead of being named, an adopted bot's
 * pre-takeover history being *claimed* as a term rather than reported as
 * unaccounted, and a fold with nothing in it printing `$0.00`.
 */

import { describe, expect, it } from "vitest";

import type { DeedIndex } from "@/lib/agent-attribution";
import type { ControllerInfo } from "@/lib/api";
import {
  DEFAULT_GROUPING,
  buildTree,
  foldLeaves,
  indexTree,
  leafFromController,
  type PerfLeaf,
} from "@/lib/perf-tree";
import {
  agentScope,
  botScope,
  isPseudoRunKey,
  reconcile,
  splitRunKey,
} from "./reconcile";

/** Everything is already in the display currency in these tests. */
const identity = (value: number) => value;
const NOW = Date.parse("2026-09-04T12:00:00Z");
const HOUR = 3_600_000;

const NO_DEEDS: DeedIndex = { bots: {}, since: 0 };

function controller(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: "pmm_1",
    bot_name: "brigado-brl_mm-btc",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    volume_traded: 0,
    close_type_counts: {},
    positions: [],
    ...over,
  } as unknown as ControllerInfo;
}

/** One owned controller leaf: `net` in its own quote, attributed to `runKey`. */
function owned(
  runKey: string,
  over: {
    net?: number;
    volume?: number;
    bot?: string;
    id?: string;
    how?: PerfLeaf["how"];
    startedAt?: number;
  } = {},
): PerfLeaf {
  const leaf = leafFromController(
    controller({
      controller_id: over.id ?? `ctrl_${over.net ?? 0}`,
      bot_name: over.bot ?? "brigado-brl_mm-btc",
      global_pnl_quote: over.net ?? 0,
      volume_traded: over.volume ?? 1_000,
      deployed_at: new Date(over.startedAt ?? NOW - 4 * HOUR).toISOString(),
    } as Partial<ControllerInfo>),
    runKey,
    over.how ?? "namespace",
  );
  return leaf;
}

function base(leaves: PerfLeaf[], attributed: number | null) {
  return {
    slug: "brigado",
    strategy: null,
    leaves,
    deeds: NO_DEEDS,
    convert: identity,
    now: NOW,
    attributed,
  };
}

/** What `/bots` shows at `?scope=agent:{runKey}` — the same call it makes. */
function botsPageNet(leaves: PerfLeaf[], runKey: string): number {
  const tree = buildTree(leaves, "All", { grouping: DEFAULT_GROUPING, deeds: NO_DEEDS });
  const node = indexTree(tree).get(agentScope(runKey));
  return foldLeaves(node?.leaves ?? [], identity, NOW).net;
}

describe("run keys", () => {
  it("splits an agent from its strategy", () => {
    expect(splitRunKey("brigado.brl_mm")).toEqual({ agent: "brigado", strategy: "brl_mm" });
  });

  it("knows the three reserved slugs from every other one", () => {
    expect(isPseudoRunKey("brigado.chat")).toBe(true);
    expect(isPseudoRunKey("brigado.delegation")).toBe(true);
    expect(isPseudoRunKey("brigado.ui")).toBe(true);
    expect(isPseudoRunKey("brigado.brl_mm")).toBe(false);
  });
});

describe("the headline is the fleet's own number", () => {
  it("equals /bots at ?scope=agent:{runKey} on the same records", () => {
    const leaves = [
      owned("brigado.brl_mm", { net: 40, id: "a" }),
      owned("brigado.brl_mm", { net: 24, id: "b" }),
      // Somebody else's trading, on the same fleet, must not leak in.
      owned("condor.other", { net: 900, id: "c", bot: "condor-other-1" }),
    ];
    const r = reconcile(base(leaves, null));

    expect(r.fold).toBe(64);
    expect(r.fold).toBe(botsPageNet(leaves, "brigado.brl_mm"));
    expect(r.runKeys).toEqual(["brigado.brl_mm"]);
  });

  it("does not count a live executor twice under its own controller", () => {
    // The accounting spine: a controller stands for what works under it. A
    // headline that summed the leaves directly would report this twice.
    const ctrl = owned("brigado.brl_mm", { net: 30, id: "pmm_1" });
    const r = reconcile(base([ctrl], 30));
    expect(r.fold).toBe(30);
    expect(r.totals.count).toBe(1);
  });

  it("narrows to one strategy when the URL names one", () => {
    const leaves = [
      owned("brigado.brl_mm", { net: 40, id: "a" }),
      owned("brigado.usd_mm", { net: 11, id: "b", bot: "brigado-usd_mm-1" }),
    ];
    expect(reconcile(base(leaves, null)).fold).toBe(51);
    expect(reconcile({ ...base(leaves, null), strategy: "brl_mm" }).fold).toBe(40);
  });
});

describe("the reconciliation", () => {
  it("reconciles to zero unaccounted when every record is run-owned", () => {
    const leaves = [
      owned("brigado.brl_mm", { net: 40, id: "a" }),
      owned("brigado.brl_mm", { net: 24, id: "b" }),
    ];
    const r = reconcile(base(leaves, 64));

    expect(r.attributed).toBe(64);
    expect(r.terms).toEqual([]);
    expect(r.unaccounted).toBe(0);
    expect(r.leads).toEqual([]);
  });

  it("names a chat-deployed bot rather than leaving it in the residual", () => {
    // A chat writes no session ledger, so the rollup cannot contain a cent of
    // this — which is exactly what makes the whole fold of that scope an exact
    // term rather than an approximation.
    const leaves = [
      owned("brigado.brl_mm", { net: 64, id: "a" }),
      owned("brigado.chat", { net: 27, id: "c", bot: "sol_scalper", how: "deed" }),
    ];
    const r = reconcile(base(leaves, 64));

    expect(r.fold).toBe(91);
    expect(r.terms).toHaveLength(1);
    expect(r.terms[0].label).toBe("Deployed from chat");
    expect(r.terms[0].delta).toBe(27);
    expect(r.terms[0].scope).toBe("agent:brigado.chat");
    expect(r.terms[0].count).toBe(1);
    expect(r.unaccounted).toBe(0);
  });

  it("keeps a chat's deploys when the loop scope is narrowed to one strategy", () => {
    // A chat's records belong to the agent and to no strategy, so narrowing
    // `?strategy=` cannot make them stop being this agent's money.
    const leaves = [
      owned("brigado.brl_mm", { net: 64, id: "a" }),
      owned("brigado.usd_mm", { net: 5, id: "b", bot: "brigado-usd_mm-1" }),
      owned("brigado.chat", { net: 27, id: "c", bot: "sol_scalper", how: "deed" }),
    ];
    const r = reconcile({ ...base(leaves, 64), strategy: "brl_mm" });

    expect(r.runKeys).toEqual(["brigado.brl_mm", "brigado.chat"]);
    expect(r.fold).toBe(91);
    expect(r.terms.map((t) => t.label)).toEqual(["Deployed from chat"]);
  });

  it("names a delegation and the dashboard apart from a chat", () => {
    const leaves = [
      owned("brigado.chat", { net: 10, id: "a", bot: "one", how: "deed" }),
      owned("brigado.delegation", { net: 20, id: "b", bot: "two", how: "deed" }),
      owned("brigado.ui", { net: 30, id: "c", bot: "three", how: "deed" }),
    ];
    const r = reconcile(base(leaves, 0));

    expect(r.terms.map((t) => t.label)).toEqual([
      "Deployed from chat",
      "Deployed by a delegation",
      "Deployed from the dashboard",
    ]);
    expect(r.terms.map((t) => t.delta)).toEqual([10, 20, 30]);
    expect(r.unaccounted).toBe(0);
  });

  it("reports an adopted bot's gap as unaccounted, with a lead, never as a term", () => {
    // The fold has this bot's whole history; the rollup has only the part
    // inside an owner window. Splitting the record at the takeover instant
    // would be a third attribution engine, so the difference is reported as a
    // residual that points at the record — not claimed as a number.
    const leaves = [
      owned("brigado.brl_mm", { net: 64, id: "a" }),
      owned("brigado.brl_mm", { net: 30, id: "b", bot: "old_hand_bot", how: "declared" }),
    ];
    const r = reconcile(base(leaves, 74));

    expect(r.fold).toBe(94);
    expect(r.terms).toEqual([]);
    expect(r.unaccounted).toBe(20);
    expect(r.leads).toHaveLength(1);
    expect(r.leads[0].scope).toBe(botScope("old_hand_bot"));
    expect(r.leads[0].label).toContain("old_hand_bot");
    expect(r.leads[0].count).toBe(1);
  });

  it("adds up: fold − attributed − Σ terms is exactly the residual", () => {
    const leaves = [
      owned("brigado.brl_mm", { net: 64, id: "a" }),
      owned("brigado.chat", { net: 27, id: "c", bot: "sol_scalper", how: "deed" }),
      owned("brigado.brl_mm", { net: 30, id: "b", bot: "old_hand_bot", how: "declared" }),
    ];
    const r = reconcile(base(leaves, 74));

    const named = r.terms.reduce((sum, t) => sum + t.delta, 0);
    expect(r.fold - (r.attributed ?? 0) - named - r.unaccounted).toBe(0);
    expect(r.unaccounted).toBe(20);
  });

  it("states no residual at all while the rollup has not arrived", () => {
    const r = reconcile(base([owned("brigado.brl_mm", { net: 64 })], null));
    expect(r.attributed).toBeNull();
    expect(r.unaccounted).toBe(0);
  });
});

describe("the dash rule", () => {
  it("reports nothing for an agent with no records at all", () => {
    const r = reconcile(base([], null));
    expect(r.reported).toBe(false);
    expect(r.fold).toBe(0);
    expect(r.runKeys).toEqual([]);
  });

  it("reports a real zero when there is volume behind it", () => {
    const r = reconcile(base([owned("brigado.brl_mm", { net: 0, volume: 5_000 })], 0));
    expect(r.reported).toBe(true);
    expect(r.totals.volume).toBe(5_000);
  });
});
