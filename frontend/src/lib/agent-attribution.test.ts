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
  inNamespace,
  ownerLabel,
  ownerOf,
  stripDeploySuffix,
  type FleetOwner,
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

  it("name the agent and the strategy as they are written", () => {
    expect(ownerLabel(owners, "brigado.brl_mm")).toBe("Brigado / BRL MM");
  });

  it("fall back to the run key's own halves for an owner that is gone", () => {
    expect(ownerLabel(owners, "ghost.strategy")).toBe("ghost / strategy");
    expect(ownerLabel(owners, "nonsense")).toBe("nonsense");
  });

  it("fall back to slugs when the map carries no names", () => {
    const bare = [owner({ agentName: "", strategyName: "" })];
    expect(ownerLabel(bare, "brigado.brl_mm")).toBe("brigado / brl_mm");
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
