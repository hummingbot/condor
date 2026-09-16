/**
 * The prepared attributor answers exactly what the per-record one did (PERF-331).
 *
 * `attributionIndex` exists to stop a fold paying for the owner list once per
 * record — an array copy, a sort and an `Array.includes` scan across every
 * owner, inside a loop over every executor the fleet has ever had. That is only
 * a safe trade if the prepared closure is the *same rule*: the parity block
 * below pins the answer against `attributionOf` case for case, including the
 * two orderings the shortcut could plausibly get wrong (longest namespace wins,
 * first owner wins a shared agent id), and the counting block pins the saving
 * itself so a future edit cannot quietly put the work back inside the loop.
 *
 * The counters wrap the *real* sort and the *real* `agentIds` read — nothing is
 * stubbed — so they measure the code that ships.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  attributionIndex,
  attributionOf,
  type Attribution,
  type DeedIndex,
  type FleetOwner,
} from "@/lib/agent-attribution";
import type { ControllerInfo, ExecutorInfo } from "@/lib/api";
import { runningLeaves } from "@/lib/perf-population";

function owner(over: Partial<FleetOwner> = {}): FleetOwner {
  return {
    runKey: "brigado.brl_mm",
    agentSlug: "brigado",
    agentName: "Brigado",
    strategySlug: "brl_mm",
    strategyName: "BRL MM",
    namespace: "brigado-brl_mm",
    declaredBots: [],
    agentIds: [],
    live: null,
    ...over,
  };
}

function deeds(bots: Record<string, string>, since = 0): DeedIndex {
  return {
    bots: Object.fromEntries(
      Object.entries(bots).map(([name, runKey]) => [name, { runKey, runId: "ui", at: 1 }]),
    ),
    since,
  };
}

const OWNERS: FleetOwner[] = [
  owner(),
  owner({
    runKey: "brigado.brl_mm_btc",
    strategySlug: "brl_mm_btc",
    // Longer namespace, listed *second*: only the length ordering can find it.
    namespace: "brigado-brl_mm-btc",
    agentIds: ["brigado.brl_mm_btc_1"],
  }),
  owner({
    runKey: "river.scalper",
    agentSlug: "river",
    strategySlug: "scalper",
    namespace: "river-scalper",
    declaredBots: ["old_hand_bot"],
    agentIds: ["river.scalper_3", "shared_tag_9"],
  }),
  owner({
    runKey: "condor.chat",
    agentSlug: "condor",
    strategySlug: "chat",
    namespace: "condor-chat",
    // The same tag a *later* owner also claims: first owner must win, which is
    // what the linear scan this Map replaces did.
    agentIds: ["shared_tag_9"],
  }),
];

const DEEDS = deeds({ "pmm-king-btcbrl": "condor.ui", "chat-bot": "condor.chat" });

/**
 * `[bot, controllerId, the answer]`.
 *
 * The answer is written out rather than only compared against `attributionOf`,
 * because `attributionOf` is now a wrapper over the thing under test: an
 * equality check alone would hold no matter what either of them said. These are
 * the answers the per-record implementation gave.
 */
const CASES: Array<[string, string, Attribution]> = [
  ["brigado-brl_mm", "", { runKey: "brigado.brl_mm", how: "namespace" }],
  // Longest namespace wins, and it is the *second* owner in the list.
  ["brigado-brl_mm-btc", "", { runKey: "brigado.brl_mm_btc", how: "namespace" }],
  [
    "brigado-brl_mm-btc-20260731-101500",
    "",
    { runKey: "brigado.brl_mm_btc", how: "namespace" },
  ],
  // `_v2` is a different slug: `-` delimits the namespace, `_` does not.
  ["brigado-brl_mm_v2", "", { runKey: "", how: "none" }],
  ["old_hand_bot", "", { runKey: "river.scalper", how: "declared" }],
  ["old_hand_bot-20260731-101500", "", { runKey: "river.scalper", how: "declared" }],
  ["", "brigado.brl_mm_btc_1", { runKey: "brigado.brl_mm_btc", how: "namespace" }],
  // Two owners claim this tag; the first in the list wins, as the scan did.
  ["", "shared_tag_9", { runKey: "river.scalper", how: "namespace" }],
  ["", "river.scalper_3", { runKey: "river.scalper", how: "namespace" }],
  ["", "nobody_1", { runKey: "", how: "none" }],
  ["", "  ", { runKey: "", how: "none" }],
  ["pmm-king-btcbrl", "", { runKey: "condor.ui", how: "deed" }],
  // A redeploy's double suffix: the chain finds the recorded name.
  [
    "pmm-king-btcbrl-20260903-181000-20260903-151237",
    "",
    { runKey: "condor.ui", how: "deed" },
  ],
  ["chat-bot-btc", "", { runKey: "", how: "none" }],
  ["chat-bot-20260731-101500", "", { runKey: "condor.chat", how: "deed" }],
  ["some-hand-rolled-bot", "", { runKey: "", how: "none" }],
  ["", "", { runKey: "", how: "none" }],
];

describe("attributionIndex answers exactly what attributionOf answers", () => {
  it.each(CASES)("bot %j, controller %j", (botName, controllerId, want) => {
    const prepared = attributionIndex(OWNERS, DEEDS)(botName, controllerId);
    expect(prepared).toEqual(want);
    expect(prepared).toEqual(attributionOf(OWNERS, DEEDS, botName, controllerId));
  });

  it("agrees with no owners and with no deeds", () => {
    for (const [botName, controllerId] of CASES) {
      expect(attributionIndex([], DEEDS)(botName, controllerId)).toEqual(
        attributionOf([], DEEDS, botName, controllerId),
      );
      expect(attributionIndex(OWNERS, null)(botName, controllerId)).toEqual(
        attributionOf(OWNERS, null, botName, controllerId),
      );
    }
  });

  it("keeps the rule order: an enforced namespace beats a deed naming the same bot", () => {
    const clash = deeds({ "brigado-brl_mm": "condor.chat" });
    expect(attributionIndex(OWNERS, clash)("brigado-brl_mm")).toEqual({
      runKey: "brigado.brl_mm",
      how: "namespace",
    });
  });

  it("defaults controllerId, like the function it replaces", () => {
    expect(attributionIndex(OWNERS, DEEDS)("brigado-brl_mm")).toEqual(
      attributionOf(OWNERS, DEEDS, "brigado-brl_mm"),
    );
  });
});

// ── The saving itself ──

function controller(n: number): ControllerInfo {
  return {
    // Owned by nobody, so every record falls through to the controller-id tag
    // rule — which is the `Array.includes` scan the Map replaces.
    bot_name: `pmm-king-${n}`,
    controller_id: `c${n}`,
    controller_name: "pmm_simple",
    connector: "binance",
    status: "running",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    global_pnl_pct: 0,
    volume_traded: 0,
    config: {},
  } as unknown as ControllerInfo;
}

function executor(n: number): ExecutorInfo {
  return {
    id: `e${n}`,
    controller_id: `c${n}`,
    type: "position_executor",
    status: "running",
    connector_name: "binance",
    trading_pair: "SOL-USDC",
    timestamp: 1,
    close_timestamp: 0,
    net_pnl_quote: 0,
    filled_amount_quote: 0,
    cum_fees_quote: 0,
    is_active: true,
    config: {},
  } as unknown as ExecutorInfo;
}

const RECORDS = 60;

describe("runningLeaves prepares the owner list once, not once per record", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sorts the owner list once for the whole fold", () => {
    const sort = vi.spyOn(Array.prototype, "sort");
    const leaves = runningLeaves({
      controllers: Array.from({ length: RECORDS }, (_, i) => controller(i)),
      executors: Array.from({ length: RECORDS }, (_, i) => executor(i)),
      owners: OWNERS,
      deeds: DEEDS,
    });
    const sorts = sort.mock.calls.length;
    vi.restoreAllMocks();

    expect(leaves).toHaveLength(RECORDS * 2);
    // Once per record — the shape this item removes — would be 120.
    expect(sorts).toBe(1);
  });

  it("reads each owner's agentIds once for the whole fold", () => {
    let reads = 0;
    const counted = OWNERS.map((o) => {
      const ids = o.agentIds;
      return {
        ...o,
        get agentIds() {
          reads += 1;
          return ids;
        },
      } as FleetOwner;
    });

    runningLeaves({
      controllers: Array.from({ length: RECORDS }, (_, i) => controller(i)),
      executors: Array.from({ length: RECORDS }, (_, i) => executor(i)),
      owners: counted,
      deeds: DEEDS,
    });

    // One read per owner, at index time. The `Array.includes` scan it replaces
    // read every owner again for every executor that reached the tag rule.
    expect(reads).toBe(counted.length);
  });
});
