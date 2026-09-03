import { describe, expect, it } from "vitest";

import {
  actionsByTick,
  beatState,
  formatDuration,
  formatRunId,
  hasPricedMoney,
  isLiveRun,
  parseRunId,
  runDurationSec,
  runFacts,
  runLabel,
} from "./runs";
import type { AgentActionRow } from "@/lib/agent-attribution";
import type { AgentRunRow } from "@/lib/api";

function run(over: Partial<AgentRunRow> = {}): AgentRunRow {
  return {
    run_id: "s1",
    kind: "session",
    number: 1,
    agent_id: "brigado.brl_mm_1",
    status: "stopped",
    execution_mode: "",
    tick_count: 0,
    snapshot_count: 0,
    started_at: 1_000_000,
    ended_at: 1_000_000,
    error: false,
    has_actions_log: true,
    strategy_slug: "brl_mm",
    strategy_name: "BRL MM",
    ...over,
  };
}

function deed(over: Partial<AgentActionRow> = {}): AgentActionRow {
  return {
    tick: 1,
    at: 0,
    tool: "stop_executor",
    verb: "stop_executor",
    summary: "Stop executor e1",
    ok: true,
    error: "",
    ...over,
  };
}

describe("run ids round-trip through the URL", () => {
  it("parses both kinds", () => {
    expect(parseRunId("s3")).toEqual({ kind: "session", number: 3 });
    expect(parseRunId("e12")).toEqual({ kind: "experiment", number: 12 });
  });

  it("refuses anything that is not one", () => {
    for (const bad of ["", null, undefined, "s", "3", "x3", "s0", "s-1", "s3x"]) {
      expect(parseRunId(bad)).toBeNull();
    }
  });

  it("formats back to what it parsed", () => {
    for (const id of ["s1", "s42", "e7"]) {
      expect(formatRunId(parseRunId(id)!)).toBe(id);
    }
  });
});

describe("a run says what kind it is", () => {
  it("names a dry run and a single tick apart", () => {
    expect(runLabel(run({ kind: "session", number: 3 }))).toBe("S3");
    expect(
      runLabel(run({ kind: "experiment", number: 1, execution_mode: "dry_run" })),
    ).toBe("D1");
    expect(
      runLabel(run({ kind: "experiment", number: 2, execution_mode: "run_once" })),
    ).toBe("R2");
    expect(runLabel(run({ kind: "experiment", number: 5, execution_mode: "" }))).toBe("E5");
  });

  it("counts running and paused as live", () => {
    expect(isLiveRun(run({ status: "running" }))).toBe(true);
    expect(isLiveRun(run({ status: "paused" }))).toBe(true);
    expect(isLiveRun(run({ status: "interrupted" }))).toBe(false);
    expect(isLiveRun(run({ status: "idle" }))).toBe(false);
  });
});

describe("duration", () => {
  it("measures a closed run between its own two stamps", () => {
    expect(runDurationSec(run({ started_at: 100, ended_at: 15_220 }), 99_999)).toBe(15_120);
  });

  it("measures a live run against the clock", () => {
    expect(runDurationSec(run({ started_at: 100, ended_at: null }), 3_700)).toBe(3_600);
  });

  it("has no duration for a run with no start", () => {
    expect(runDurationSec(run({ started_at: null }), 5_000)).toBeNull();
    expect(runDurationSec(run({ started_at: 0 }), 5_000)).toBeNull();
  });

  it("formats compactly", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(720)).toBe("12m");
    expect(formatDuration(15_120)).toBe("4h12m");
    expect(formatDuration(90_000)).toBe("1d1h");
    expect(formatDuration(null)).toBe("");
  });

  it("says ticks alone when there is no duration to say", () => {
    expect(runFacts(run({ tick_count: 20, started_at: 100, ended_at: 15_220 }), 0)).toBe(
      "20 ticks · 4h12m",
    );
    expect(runFacts(run({ tick_count: 1, started_at: null }), 0)).toBe("1 tick");
  });
});

describe("money that was never priced is not zero", () => {
  it("is absent for a run the pipeline answered with all zeros", () => {
    expect(hasPricedMoney(null)).toBe(false);
    expect(hasPricedMoney(undefined)).toBe(false);
    expect(
      hasPricedMoney({
        total_pnl: 0,
        realized_pnl: 0,
        unrealized_pnl: 0,
        volume: 0,
        fees: 0,
        trade_count: 0,
        open_count: 0,
      }),
    ).toBe(false);
  });

  it("is present for a run that broke exactly even but did trade", () => {
    expect(hasPricedMoney({ total_pnl: 0, volume: 42_000 })).toBe(true);
    expect(hasPricedMoney({ total_pnl: 0, open_count: 2 })).toBe(true);
    expect(hasPricedMoney({ total_pnl: -12.5 })).toBe(true);
  });
});

describe("the beat rule", () => {
  it("is red when any deed on the tick failed", () => {
    expect(
      beatState({
        actions: [deed(), deed({ ok: false, error: "rejected" })],
        journalActions: 2,
        hasActionsLog: true,
      }),
    ).toBe("failed");
  });

  it("is green when deeds ran and all worked", () => {
    expect(
      beatState({ actions: [deed(), deed()], journalActions: 2, hasActionsLog: true }),
    ).toBe("ok");
  });

  it("is hollow when the run keeps a log and the tick did nothing", () => {
    expect(
      beatState({ actions: [], journalActions: 0, hasActionsLog: true }),
    ).toBe("idle");
  });

  it("is unlogged for a run written before the action log existed", () => {
    // Every session on disk today lands here: `actions=0` in the journal and no
    // `actions.jsonl` at all. Calling that "did nothing" is the bug.
    expect(
      beatState({ actions: [], journalActions: 0, hasActionsLog: false }),
    ).toBe("unlogged");
  });

  it("is unlogged when the journal claims deeds the log cannot show", () => {
    expect(
      beatState({ actions: [], journalActions: 3, hasActionsLog: true }),
    ).toBe("unlogged");
  });
});

describe("deeds join to ticks", () => {
  it("buckets rows by tick, keeping order within a tick", () => {
    const byTick = actionsByTick([
      deed({ tick: 1, verb: "a" }),
      deed({ tick: 3, verb: "b" }),
      deed({ tick: 1, verb: "c" }),
    ]);
    expect(byTick.get(1)?.map((r) => r.verb)).toEqual(["a", "c"]);
    expect(byTick.get(3)?.map((r) => r.verb)).toEqual(["b"]);
    expect(byTick.get(2)).toBeUndefined();
  });
});
