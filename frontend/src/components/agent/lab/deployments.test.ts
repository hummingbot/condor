import { describe, expect, it } from "vitest";

import {
  fleetHref,
  formatTick,
  kindIcon,
  liveLabel,
  orderDeployments,
  runFleetHref,
} from "./deployments";
import type { DeploymentRow } from "@/lib/api";

function row(over: Partial<DeploymentRow> = {}): DeploymentRow {
  return {
    kind: "bot",
    label: "ag-st",
    detail: "deployed",
    created_tick: null,
    started_at: 1_000,
    ended_at: null,
    live: true,
    pnl: 0,
    volume: 0,
    scope: "bot:ag-st-20260807-022100",
    ...over,
  };
}

describe("orderDeployments", () => {
  it("reads bots, then their controllers, then standalone executors", () => {
    const rows = [
      row({ kind: "executor", label: "grid SOL-USDC" }),
      row({ kind: "controller", label: "c1" }),
      row({ kind: "bot", label: "ag-st" }),
    ];
    expect(orderDeployments(rows).map((r) => r.label)).toEqual([
      "ag-st",
      "c1",
      "grid SOL-USDC",
    ]);
  });

  it("keeps each kind in the order it happened, oldest first", () => {
    const rows = [
      row({ kind: "executor", label: "late", started_at: 3_000 }),
      row({ kind: "executor", label: "early", started_at: 2_000 }),
    ];
    expect(orderDeployments(rows).map((r) => r.label)).toEqual(["early", "late"]);
  });

  it("does not mutate what it was handed", () => {
    const rows = [row({ kind: "executor" }), row({ kind: "bot" })];
    orderDeployments(rows);
    expect(rows[0].kind).toBe("executor");
  });
});

describe("kindIcon", () => {
  it("names each kind apart", () => {
    const icons = [kindIcon("bot"), kindIcon("controller"), kindIcon("executor")];
    expect(new Set(icons).size).toBe(3);
  });
});

describe("liveLabel", () => {
  it("says live while the run still holds it", () => {
    expect(liveLabel(row({ live: true }))).toBe("live");
  });

  it("says closed for a bot released mid-run, whatever its snapshot claims", () => {
    // The instance is still deployed and its performance snapshot still says
    // "running" — but this run handed it over, so it is closed *to this run*.
    expect(liveLabel(row({ live: false, ended_at: 5_000 }))).toBe("closed");
  });
});

describe("formatTick", () => {
  it("names the tick that created the row", () => {
    expect(formatTick(10)).toBe("tick 10");
  });

  it("leaves an unjoinable tick blank rather than guessing zero", () => {
    expect(formatTick(null)).toBe("—");
  });
});

describe("fleetHref", () => {
  it("links a bot, a controller and an executor to their own fleet node", () => {
    expect(fleetHref(row({ scope: "bot:ag-st-1" }))).toBe("/bots?scope=bot%3Aag-st-1");
    expect(fleetHref(row({ scope: "ctrl:ag-st-1:c1" }))).toBe(
      "/bots?scope=ctrl%3Aag-st-1%3Ac1",
    );
    expect(fleetHref(row({ scope: "exec:e1" }))).toBe("/bots?scope=exec%3Ae1");
  });

  it("offers no link for a row with no address", () => {
    expect(fleetHref(row({ scope: "" }))).toBeNull();
  });

  it("carries the run along, so stepping up to the agent keeps it (FEAT-101)", () => {
    expect(fleetHref(row({ scope: "bot:ag-st-1" }), 3)).toBe(
      "/bots?scope=bot%3Aag-st-1&run=s3",
    );
  });

  it("leaves the link alone when there is no run to carry", () => {
    expect(fleetHref(row({ scope: "bot:ag-st-1" }), null)).toBe("/bots?scope=bot%3Aag-st-1");
    expect(fleetHref(row({ scope: "bot:ag-st-1" }), 0)).toBe("/bots?scope=bot%3Aag-st-1");
  });
});

describe("runFleetHref", () => {
  it("opens the agent's scope narrowed to this run, not the strategy's lifetime", () => {
    expect(runFleetHref("ag.st", 3)).toBe("/bots?scope=agent%3Aag.st&run=s3");
  });

  it("offers nothing without an owner or without a run", () => {
    expect(runFleetHref("", 3)).toBeNull();
    expect(runFleetHref("ag.st", 0)).toBeNull();
  });
});
