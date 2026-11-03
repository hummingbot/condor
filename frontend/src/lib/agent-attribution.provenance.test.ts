/**
 * Who made this, and how we know (FEAT-106).
 *
 * The order is the feature. Two of the three rules are *enforced* — the tick's
 * permission callback refused everything outside a namespace, and
 * `create_*_executor` refused every untagged `controller_id` — so they cannot be
 * wrong. The third is *observed*: it reports a record Condor kept, and a record
 * can be stale, because a bot can be destroyed and its name reused. So every
 * case here is a way an observed answer could beat an enforced one, or a way a
 * row could claim to be a proof when it is only a report.
 */

import { describe, expect, it } from "vitest";

import {
  agentOfBot,
  attributionOf,
  DEED_TITLE,
  deployNameChain,
  provenanceOf,
  type DeedIndex,
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

function deeds(bots: Record<string, string>, since = 1_000): DeedIndex {
  const index: DeedIndex = { bots: {}, since };
  for (const [name, runKey] of Object.entries(bots)) {
    index.bots[name] = { runKey, runId: "c_a1b2", at: since };
  }
  return index;
}

describe("the priority order", () => {
  it("credits a chat's deploy to the chat, which is the whole point", () => {
    const att = attributionOf([], deeds({ "pmm-king-btcbrl": "condor.chat" }), "pmm-king-btcbrl");
    expect(att).toEqual({ runKey: "condor.chat", how: "deed" });
  });

  it("lets a namespace beat a deed naming the same bot", () => {
    // The name-reuse case: a stale record must never outrank a live rule.
    const att = attributionOf(
      [owner()],
      deeds({ "brigado-brl_mm-btc": "condor.chat" }),
      "brigado-brl_mm-btc",
    );
    expect(att).toEqual({ runKey: "brigado.brl_mm", how: "namespace" });
  });

  it("lets a declared legacy name beat a deed too", () => {
    const owners = [owner({ declaredBots: ["old_hand_bot"] })];
    const att = attributionOf(owners, deeds({ old_hand_bot: "condor.ui" }), "old_hand_bot");
    expect(att).toEqual({ runKey: "brigado.brl_mm", how: "declared" });
  });

  it("lets an executor's enforced tag beat a deed on its bot", () => {
    const owners = [owner({ agentIds: ["brigado.brl_mm_7"] })];
    const att = attributionOf(
      owners,
      deeds({ "(unattached)": "condor.chat" }),
      "(unattached)",
      "brigado.brl_mm_7",
    );
    expect(att).toEqual({ runKey: "brigado.brl_mm", how: "namespace" });
  });

  it("is nobody's, and says so, when no rule and no record answers", () => {
    expect(attributionOf([owner()], deeds({}), "some-hand-rolled-bot")).toEqual({
      runKey: "",
      how: "none",
    });
    expect(attributionOf([], null, "anything")).toEqual({ runKey: "", how: "none" });
    expect(attributionOf([], deeds({ a: "condor.chat" }), "")).toEqual({ runKey: "", how: "none" });
  });
});

describe("what a deed is matched on", () => {
  it("matches the base a deploy was asked for, not the instance it became", () => {
    const index = deeds({ "chat-bot": "condor.chat" });
    expect(attributionOf([], index, "chat-bot-20260731-101500").how).toBe("deed");
    expect(attributionOf([], index, "chat-bot-20260901-000000").runKey).toBe("condor.chat");
  });

  it("is exact on the base: a family is a namespace idea, not a deed's", () => {
    // `inNamespace` widens a *declared* name to its tagged siblings because the
    // runtime does. A deed names one bot, and a differently-named bot beside it
    // is a different bot — widening here would credit a run with work it never
    // recorded doing.
    expect(attributionOf([], deeds({ "chat-bot": "condor.chat" }), "chat-bot-btc").how).toBe(
      "none",
    );
  });

  it("carries the three pseudo-runs through untouched", () => {
    const index = deeds({ a: "condor.chat", b: "brigado.delegation", c: "condor.ui" });
    expect(attributionOf([], index, "a").runKey).toBe("condor.chat");
    expect(attributionOf([], index, "b").runKey).toBe("brigado.delegation");
    expect(attributionOf([], index, "c").runKey).toBe("condor.ui");
  });
});

describe("agentOfBot", () => {
  it("is still the two enforced rules and nothing else", () => {
    // The old entry point delegates now, so this pins that the delegation did
    // not quietly hand it a third rule: no deed index, no deed answers.
    expect(agentOfBot([owner()], "brigado-brl_mm-btc")).toBe("brigado.brl_mm");
    expect(agentOfBot([owner()], "pmm-king-btcbrl")).toBe("");
  });
});

describe("provenanceOf", () => {
  it("reports a row as deed-attributed only when every leaf under it agrees", () => {
    expect(provenanceOf([{ how: "deed" }, { how: "deed" }])).toBe("deed");
    expect(provenanceOf([{ how: "deed" }, { how: "namespace" }])).toBe("namespace");
  });

  it("claims nothing about an empty row", () => {
    expect(provenanceOf([])).toBe("none");
  });

  it("says out loud what the marker means", () => {
    expect(DEED_TITLE).toBe("attributed by a recorded deed, not by name");
  });
});

// ── The redeployed bot (the `pmm-king-btcbrl` case) ──
//
// A deploy stamps `-YYYYMMDD-HHMMSS` onto the name it was given, so redeploying
// a bot that already carries one stamps a second. The deed index is keyed by
// the name the agent asked for, and a single strip stopped reaching it from the
// first redeploy on — which is the bot that has been running longest, and so
// the one with the most trading behind it. It arrived at `/bots` credited to
// nobody, and the owner level collapsed for want of a second bucket.

describe("deployNameChain", () => {
  it("keeps the name it was given, then every name it can have come from", () => {
    expect(deployNameChain("pmm-king-btcbrl-20260903-181000-20260903-151237")).toEqual([
      "pmm-king-btcbrl-20260903-181000-20260903-151237",
      "pmm-king-btcbrl-20260903-181000",
      "pmm-king-btcbrl",
    ]);
  });

  it("is the two names a bot deployed once has always had", () => {
    expect(deployNameChain("alpha-20260731-101500")).toEqual([
      "alpha-20260731-101500",
      "alpha",
    ]);
    expect(deployNameChain("alpha")).toEqual(["alpha"]);
    expect(deployNameChain("")).toEqual([]);
  });

  it("does not mistake a partial stamp for one", () => {
    // `-20260901-2315` is four digits short of a deploy suffix, and a bot may
    // legitimately be named that way.
    expect(deployNameChain("pmm-king-btcbrl-20260901-2315")).toEqual([
      "pmm-king-btcbrl-20260901-2315",
    ]);
  });
});

describe("a redeployed bot still reaches its deed", () => {
  it("credits the run that asked for the name, two stamps ago", () => {
    const att = attributionOf(
      [],
      deeds({ "pmm-king-btcbrl": "brigado.pmm_king_btc_brl_fleet_operator" }),
      "pmm-king-btcbrl-20260903-181000-20260903-151237",
    );
    expect(att).toEqual({
      runKey: "brigado.pmm_king_btc_brl_fleet_operator",
      how: "deed",
    });
  });

  it("prefers the most specific record when the index holds several", () => {
    const att = attributionOf(
      [],
      deeds({
        "pmm-king-btcbrl": "condor.chat",
        "pmm-king-btcbrl-20260903-181000": "brigado.pmm_king_btc_brl_fleet_operator",
      }),
      "pmm-king-btcbrl-20260903-181000-20260903-151237",
    );
    expect(att.runKey).toBe("brigado.pmm_king_btc_brl_fleet_operator");
  });

  it("still lets an enforced rule beat every name in the chain", () => {
    const att = attributionOf(
      [owner({ namespace: "brigado-brl_mm" })],
      deeds({ "brigado-brl_mm-btc": "condor.chat" }),
      "brigado-brl_mm-btc-20260903-181000-20260903-151237",
    );
    expect(att).toEqual({ runKey: "brigado.brl_mm", how: "namespace" });
  });
});
