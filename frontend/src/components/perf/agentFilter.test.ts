import { describe, expect, it } from "vitest";

import {
  agentOptions,
  inRun,
  matchesAgents,
  parseRunParam,
  runChipLabel,
  runOwner,
  runParam,
  runRecords,
  UNATTRIBUTED,
  UNATTRIBUTED_LABEL,
} from "./agentFilter";
import type { DeploymentRow } from "@/lib/api";
import type { PerfLeaf } from "@/lib/perf-tree";

function leaf(over: Partial<PerfLeaf> = {}): PerfLeaf {
  return {
    id: "ctrl-1",
    kind: "controller",
    label: "ctrl-1",
    bot: "brigado-brl_mm-20260807-022130",
    agent: "brigado.brl_mm",
    controllerId: "ctrl-1",
    executorType: "pmm_simple",
    connector: "binance",
    pair: "BTC-BRL",
    realized: 0,
    unrealized: 0,
    net: 0,
    volume: 0,
    fees: 0,
    capital: 0,
    closeTypes: {},
    positions: [],
    startedAt: null,
    endedAt: null,
    running: true,
    status: "running",
    source: {} as PerfLeaf["source"],
    ...over,
  };
}

function row(over: Partial<DeploymentRow> = {}): DeploymentRow {
  return {
    kind: "bot",
    label: "brigado-brl_mm",
    detail: "deployed",
    created_tick: 2,
    started_at: 1_000,
    ended_at: null,
    live: true,
    pnl: 0,
    volume: 0,
    scope: "bot:brigado-brl_mm-20260807-022130",
    ...over,
  };
}

describe("agentOptions", () => {
  it("offers one bubble per attributed owner, with its count", () => {
    const options = agentOptions([
      leaf({ agent: "brigado.brl_mm" }),
      leaf({ agent: "brigado.brl_mm" }),
      leaf({ agent: "alpha.scalper" }),
    ]);
    expect(options.map((o) => [o.value, o.label, o.count])).toEqual([
      ["alpha.scalper", "alpha / scalper", 1],
      ["brigado.brl_mm", "brigado / brl_mm", 2],
    ]);
  });

  it("buckets the leaves nobody owns under Unattributed, last", () => {
    const options = agentOptions([
      leaf({ agent: "" }),
      leaf({ agent: "" }),
      leaf({ agent: "brigado.brl_mm" }),
    ]);
    expect(options.map((o) => o.value)).toEqual(["brigado.brl_mm", UNATTRIBUTED]);
    expect(options[1]).toEqual({ value: UNATTRIBUTED, label: UNATTRIBUTED_LABEL, count: 2 });
  });

  it("draws no Unattributed bubble when everything is attributed", () => {
    const options = agentOptions([leaf({ agent: "brigado.brl_mm" })]);
    expect(options.map((o) => o.value)).toEqual(["brigado.brl_mm"]);
  });

  it("is empty for an empty population", () => {
    expect(agentOptions([])).toEqual([]);
  });
});

describe("matchesAgents", () => {
  it("filters nothing when nothing is ticked", () => {
    expect(matchesAgents(leaf({ agent: "" }), [])).toBe(true);
    expect(matchesAgents(leaf({ agent: "brigado.brl_mm" }), [])).toBe(true);
  });

  it("keeps only the ticked owners", () => {
    expect(matchesAgents(leaf({ agent: "brigado.brl_mm" }), ["brigado.brl_mm"])).toBe(true);
    expect(matchesAgents(leaf({ agent: "alpha.scalper" }), ["brigado.brl_mm"])).toBe(false);
  });

  it("makes Unattributed a real choice, not an omission", () => {
    expect(matchesAgents(leaf({ agent: "" }), [UNATTRIBUTED])).toBe(true);
    expect(matchesAgents(leaf({ agent: "brigado.brl_mm" }), [UNATTRIBUTED])).toBe(false);
  });
});

describe("runRecords", () => {
  it("is null when the ledger has not arrived, so nothing is filtered yet", () => {
    expect(runRecords(undefined)).toBeNull();
    expect(runRecords(null)).toBeNull();
  });

  it("reads bases, controller ids and executor ids off the ledger", () => {
    const records = runRecords([
      row(),
      row({ kind: "controller", label: "cfg-7", scope: "ctrl:brigado-brl_mm-20260807-022130:cfg-7" }),
      row({ kind: "executor", label: "grid SOL-USDC", scope: "exec:x-99" }),
    ]);
    expect(records).toEqual({
      bots: ["brigado-brl_mm"],
      controllerIds: ["cfg-7"],
      executorIds: ["x-99"],
    });
  });

  it("keeps an empty ledger as records — a run that deployed nothing narrows to nothing", () => {
    expect(runRecords([])).toEqual({ bots: [], controllerIds: [], executorIds: [] });
  });
});

describe("inRun", () => {
  const records = {
    bots: ["brigado-brl_mm"],
    controllerIds: ["cfg-7"],
    executorIds: ["x-99"],
  };

  it("filters nothing without a run", () => {
    expect(inRun(leaf({ bot: "someone-else" }), null)).toBe(true);
  });

  it("matches a bot's family, so a base and its deploy instance are one bot", () => {
    expect(inRun(leaf({ bot: "brigado-brl_mm-20260807-022130", controllerId: "" }), records)).toBe(
      true,
    );
    expect(inRun(leaf({ bot: "brigado-brl_mm", controllerId: "" }), records)).toBe(true);
  });

  it("does not credit a bot that merely starts with the same word", () => {
    expect(inRun(leaf({ bot: "brigado-brl_mm2", controllerId: "" }), records)).toBe(false);
  });

  it("matches a controller by id and an executor by id", () => {
    expect(inRun(leaf({ bot: "other", controllerId: "cfg-7" }), records)).toBe(true);
    expect(inRun(leaf({ bot: "other", controllerId: "cfg-8" }), records)).toBe(false);
    expect(inRun(leaf({ kind: "executor", id: "x-99", bot: "other" }), records)).toBe(true);
    expect(inRun(leaf({ kind: "executor", id: "x-98", bot: "other" }), records)).toBe(false);
  });

  it("keeps nothing when the run's records are all on another server", () => {
    const elsewhere = { bots: ["far-away"], controllerIds: [], executorIds: [] };
    expect(inRun(leaf(), elsewhere)).toBe(false);
    expect(inRun(leaf({ kind: "executor", id: "x-99" }), elsewhere)).toBe(false);
  });
});

describe("the run parameter", () => {
  it("round-trips a session number", () => {
    expect(parseRunParam(runParam(3))).toBe(3);
  });

  it("reads anything that is not a run as no run at all", () => {
    for (const bad of [null, undefined, "", "3", "sx", "s", "s0", "session-3"]) {
      expect(parseRunParam(bad)).toBeNull();
    }
  });

  it("names the chip after the run", () => {
    expect(runChipLabel(3)).toBe("run S3 only");
  });
});

describe("runOwner", () => {
  it("reads the owner out of an agent scope", () => {
    expect(runOwner("agent:brigado.brl_mm")).toEqual({ slug: "brigado", sslug: "brl_mm" });
  });

  it("is null for any other scope, so the run narrows nothing there", () => {
    for (const scope of ["fleet", "bot:x", "ctrl:x:y", "agent:", "agent:brigado", "agent:.x", "agent:x."]) {
      expect(runOwner(scope)).toBeNull();
    }
  });
});
