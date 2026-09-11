/**
 * Whose trading is whose, pinned (FEAT-096).
 *
 * Every case here is a way `/bots` could file a row under the wrong agent — or
 * under an agent at all when nobody owns it, which is the more expensive
 * mistake: an unattributed bot is honest, and a misattributed one is a report
 * about a strategy that never traded it.
 */

import { describe, expect, it } from "vitest";

import {
  agentOfBot,
  agentOfControllerId,
  countdown,
  inNamespace,
  loopFacts,
  loopStatus,
  ownerOf,
  ownerTitle,
  runKeyLabel,
  stripDeploySuffix,
  type FleetOwner,
  type LiveLoop,
} from "./agent-attribution";

function owner(over: Partial<FleetOwner> = {}): FleetOwner {
  const agentSlug = over.agentSlug ?? "brigado";
  const strategySlug = over.strategySlug ?? "brl_mm";
  return {
    runKey: `${agentSlug}.${strategySlug}`,
    agentSlug,
    agentName: "Brigado",
    strategySlug,
    strategyName: "BRL MM",
    namespace: `${agentSlug}-${strategySlug}`,
    declaredBots: [],
    agentIds: [],
    live: null,
    ...over,
  };
}

describe("the namespace rule", () => {
  const owners = [owner()];

  it("owns the base itself", () => {
    expect(agentOfBot(owners, "brigado-brl_mm")).toBe("brigado.brl_mm");
  });

  it("owns a tagged sibling", () => {
    expect(agentOfBot(owners, "brigado-brl_mm-btc")).toBe("brigado.brl_mm");
  });

  it("owns a deployed instance, timestamp and all", () => {
    expect(agentOfBot(owners, "brigado-brl_mm-btc-20260731-101500")).toBe("brigado.brl_mm");
  });

  it("does not own a bot whose name merely starts with the slug", () => {
    // `-` delimits the namespace, so `brl_mm_v2` is a different strategy and
    // never `brl_mm`'s — this is the collision the convention exists to rule out.
    expect(agentOfBot(owners, "brigado-brl_mm_v2")).toBe("");
    expect(agentOfBot(owners, "brigado-brl_mm_v2-btc")).toBe("");
  });

  it("gives a bot to the longest namespace that claims it", () => {
    const both = [
      owner({ strategySlug: "brl", namespace: "brigado-brl" }),
      // Contrived — a slug cannot contain `-` — but the tie-break is what keeps
      // the rule from depending on the order the map came back in.
      owner({ strategySlug: "brl_mm", namespace: "brigado-brl-mm" }),
    ];
    expect(agentOfBot(both, "brigado-brl-mm-btc")).toBe("brigado.brl_mm");
  });

  it("attributes nothing to nobody", () => {
    expect(agentOfBot(owners, "some-hand-rolled-bot")).toBe("");
    expect(agentOfBot(owners, "")).toBe("");
    expect(agentOfBot([], "brigado-brl_mm")).toBe("");
  });
});

describe("the legacy escape hatch", () => {
  const owners = [owner({ agentSlug: "river", strategySlug: "scalper", declaredBots: ["old_hand_bot"] })];

  it("owns a configured name the prefix cannot prove", () => {
    expect(agentOfBot(owners, "old_hand_bot")).toBe("river.scalper");
  });

  it("owns its deployed instance too", () => {
    expect(agentOfBot(owners, "old_hand_bot-20260731-101500")).toBe("river.scalper");
  });

  it("owns its tagged siblings, exactly as the runtime's `owns()` does", () => {
    expect(agentOfBot(owners, "old_hand_bot-btc")).toBe("river.scalper");
  });

  it("loses to a namespace, which is the stronger proof", () => {
    const both = [
      owner({ agentSlug: "river", strategySlug: "scalper", declaredBots: ["brigado-brl_mm"] }),
      owner(),
    ];
    expect(agentOfBot(both, "brigado-brl_mm-btc")).toBe("brigado.brl_mm");
  });
});

describe("standalone executors", () => {
  const owners = [owner({ agentIds: ["brigado.brl_mm_6", "brigado.brl_mm_7"] })];

  it("belong to the session that tagged them", () => {
    expect(agentOfControllerId(owners, "brigado.brl_mm_7")).toBe("brigado.brl_mm");
  });

  it("belong to their strategy whichever session it was", () => {
    expect(agentOfControllerId(owners, "brigado.brl_mm_6")).toBe("brigado.brl_mm");
  });

  it("claim nothing for a controller config id", () => {
    // A controller is attributed through its bot, never through this.
    expect(agentOfControllerId(owners, "pmm_simple_1")).toBe("");
    expect(agentOfControllerId(owners, "main")).toBe("");
    expect(agentOfControllerId(owners, "")).toBe("");
  });

  it("do not match a session that never existed", () => {
    expect(agentOfControllerId(owners, "brigado.brl_mm_99")).toBe("");
  });
});

describe("labels", () => {
  const owners = [owner()];

  it("say the run key out loud, in slugs, with no map needed", () => {
    // The slug form is what the bot names beneath an agent row are built from,
    // so it is the one a reader can match by eye down the column.
    expect(runKeyLabel("brigado.brl_mm")).toBe("brigado / brl_mm");
    expect(runKeyLabel("nonsense")).toBe("nonsense");
  });

  it("spell the same subject out in display names for the tooltip", () => {
    expect(ownerTitle(owners, "brigado.brl_mm")).toBe("Brigado / BRL MM");
  });

  it("fall back to the label for an owner the map no longer holds", () => {
    expect(ownerTitle(owners, "ghost.strategy")).toBe("ghost / strategy");
  });

  it("fall back to slugs when the map carries no names", () => {
    const bare = [owner({ agentName: "", strategyName: "" })];
    expect(ownerTitle(bare, "brigado.brl_mm")).toBe("brigado / brl_mm");
  });

  it("find the owner behind a run key", () => {
    expect(ownerOf(owners, "brigado.brl_mm")?.namespace).toBe("brigado-brl_mm");
    expect(ownerOf(owners, "ghost.strategy")).toBeUndefined();
  });
});

describe("the primitives the rule is built from", () => {
  it("strips only a real deploy suffix", () => {
    expect(stripDeploySuffix("brigado-brl_mm-20260731-101500")).toBe("brigado-brl_mm");
    expect(stripDeploySuffix("brigado-brl_mm-2026")).toBe("brigado-brl_mm-2026");
    expect(stripDeploySuffix("")).toBe("");
  });

  it("says no when either side is empty", () => {
    expect(inNamespace("", "brigado-brl_mm")).toBe(false);
    expect(inNamespace("brigado-brl_mm", "")).toBe(false);
  });
});


/**
 * What the header band says the loop is doing.
 *
 * The band's job is to answer "is this thing even alive", so the cases that
 * matter are the ones where the honest answer is not a countdown: no loop at
 * all, a paused one, one that has not ticked yet, and one whose tick is running
 * long — where a raw subtraction would print a negative number and read as a
 * bug in the page rather than as a slow tick.
 */
describe("the loop's state", () => {
  const NOW_S = 1_700_000_000;
  const NOW = NOW_S * 1000;

  function loop(over: Partial<LiveLoop> = {}): LiveLoop {
    return {
      agentId: "brigado.brl_mm_7",
      sessionNum: 7,
      status: "running",
      tickCount: 214,
      lastTickAt: NOW_S - 22,
      frequencySec: 60,
      lastAction: "Spreads held.",
      lastDid: null,
      lastError: "",
      ...over,
    };
  }

  it("reads no loop at all as idle, with nothing to say about ticks", () => {
    expect(loopStatus(null)).toBe("idle");
    expect(loopFacts(null, NOW)).toEqual([]);
  });

  it("counts down to the next tick", () => {
    expect(loopFacts(loop(), NOW)).toEqual(["session 7", "tick 214", "next tick 38s"]);
  });

  it("says a long tick is overdue rather than printing a negative", () => {
    expect(loopFacts(loop({ lastTickAt: NOW_S - 75 }), NOW)).toEqual([
      "session 7",
      "tick 214",
      "tick overdue by 15s",
    ]);
  });

  it("offers no countdown for a loop that has not ticked yet", () => {
    expect(loopFacts(loop({ lastTickAt: 0, tickCount: 0 }), NOW)).toEqual([
      "session 7",
      "tick 0",
      "first tick pending",
    ]);
  });

  it("names a paused loop without pretending it is about to tick", () => {
    const paused = loop({ status: "paused" });
    expect(loopStatus(paused)).toBe("paused");
    expect(loopFacts(paused, NOW)).toEqual(["session 7", "tick 214"]);
  });

  it("reads at every scale", () => {
    expect(countdown(38)).toBe("38s");
    expect(countdown(125)).toBe("2m 05s");
    expect(countdown(3860)).toBe("1h 04m");
    expect(countdown(-5)).toBe("0s");
  });
});
