/**
 * The URL is the whole state, so the rules that read it are the feature.
 *
 * Four pages collapsed onto one route (FEAT-103), and what used to be four
 * components' worth of internal state is now four query parameters. Every
 * promise the workspace makes — that a section, a scope, a run and a tick can
 * each be pasted into a new tab and land on the same thing — is a promise about
 * this file, so it is pinned here rather than through a rendered page.
 */

import { describe, expect, it } from "vitest";

import { KNOWLEDGE_TABS } from "@/components/agent/knowledgeTabs";
import type { AgentRunRow, StrategySummary } from "@/lib/api";
import {
  DEFAULT_VIEW,
  WORKSPACE_VIEWS,
  isWorkspaceView,
  parseWorkspace,
  pickRun,
  pickStrategy,
  spineSectionFor,
} from "./views";

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
    ended_at: 1_000_100,
    error: false,
    has_actions_log: true,
    strategy_slug: "brl_mm",
    strategy_name: "BRL MM",
    ...over,
  };
}

function strategy(over: Partial<StrategySummary> = {}): StrategySummary {
  return {
    slug: "brl_mm",
    name: "BRL MM",
    description: "",
    status: "stopped",
    agent_id: "",
    session_count: 0,
    experiment_count: 0,
    tick_count: 0,
    daily_pnl: 0,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    instances: [],
    ...over,
  };
}

describe("the views", () => {
  it("are the loop's own plus the seven sections an agent is read in", () => {
    // The taxonomy is imported, never restated: two lists of the same seven
    // names is how they drift apart.
    for (const tab of KNOWLEDGE_TABS) {
      expect(WORKSPACE_VIEWS).toContain(tab);
    }
    expect(WORKSPACE_VIEWS).toContain("now");
    expect(WORKSPACE_VIEWS).toContain("tick");
  });

  it("only accept a name they actually have, off a URL", () => {
    expect(isWorkspaceView("now")).toBe(true);
    expect(isWorkspaceView("skills")).toBe(true);
    expect(isWorkspaceView("lab")).toBe(false);
    expect(isWorkspaceView(null)).toBe(false);
  });
});

describe("parsing the URL", () => {
  it("opens on Now, never on Brain", () => {
    expect(parseWorkspace("").view).toBe("now");
    expect(DEFAULT_VIEW).toBe("now");
    // A view nobody has is not an error page, it is the default one.
    expect(parseWorkspace("?view=nonsense").view).toBe("now");
  });

  it("reads a section, a scope, a run and a tick at once", () => {
    expect(parseWorkspace("?view=tick&strategy=brl_mm&run=s3&tick=7")).toEqual({
      view: "tick",
      strategy: "brl_mm",
      run: { kind: "session", number: 3 },
      tick: 7,
    });
  });

  it("leaves a selection out rather than inventing one", () => {
    expect(parseWorkspace("?view=runs")).toEqual({
      view: "runs",
      strategy: null,
      run: null,
      tick: null,
    });
  });

  it("refuses a run id and a tick that are not one", () => {
    expect(parseWorkspace("?run=session_3").run).toBeNull();
    expect(parseWorkspace("?tick=seven").tick).toBeNull();
    expect(parseWorkspace("?tick=-2").tick).toBeNull();
  });

  it("still honours the agent page's `?tab=`, which is in bookmarks", () => {
    expect(parseWorkspace("?tab=skills").view).toBe("skills");
    // `?view=` wins when both are there: one grammar goes out, and it is that.
    expect(parseWorkspace("?view=runs&tab=skills").view).toBe("runs");
  });
});

describe("the spine's current section", () => {
  it("is the view itself for everything that is a section", () => {
    expect(spineSectionFor("runs")).toBe("runs");
    expect(spineSectionFor("brain")).toBe("brain");
    expect(spineSectionFor("now")).toBe("now");
  });

  it("stays on Runs while a tick of one is open", () => {
    // A tick is a moment of a run, not a destination — so the entry you came
    // through is the one still lit, and going back up is a click on it.
    expect(spineSectionFor("tick")).toBe("runs");
  });
});

describe("the strategy in scope", () => {
  it("is the one the URL names, when the agent owns it", () => {
    const strategies = [strategy(), strategy({ slug: "usd_mm" })];
    expect(pickStrategy(strategies, [], "usd_mm")).toBe("usd_mm");
  });

  it("ignores a name this agent does not own", () => {
    expect(pickStrategy([strategy()], [], "someone_elses")).toBe("brl_mm");
  });

  it("is the running one when the URL says nothing", () => {
    const strategies = [
      strategy({ slug: "old" }),
      strategy({ slug: "live", status: "running" }),
    ];
    expect(pickStrategy(strategies, [], null)).toBe("live");
  });

  it("falls back to whichever strategy ran most recently", () => {
    const strategies = [strategy({ slug: "a" }), strategy({ slug: "b" })];
    const runs = [
      run({ strategy_slug: "a", started_at: 1_000 }),
      run({ strategy_slug: "b", started_at: 9_000 }),
    ];
    expect(pickStrategy(strategies, runs, null)).toBe("b");
  });

  it("is null for an agent with no strategies at all", () => {
    expect(pickStrategy([], [], null)).toBeNull();
  });
});

describe("the run in scope", () => {
  const runs = [
    run({ run_id: "s2", number: 2, started_at: 2_000 }),
    run({ run_id: "s1", number: 1, started_at: 1_000 }),
    run({ run_id: "e1", kind: "experiment", number: 1, strategy_slug: "usd_mm" }),
  ];

  it("is the one the URL names", () => {
    expect(pickRun(runs, "brl_mm", { kind: "session", number: 1 })?.run_id).toBe("s1");
  });

  it("is the newest in scope when the URL names none", () => {
    expect(pickRun(runs, "brl_mm", null)?.run_id).toBe("s2");
  });

  it("is the newest in scope when the URL names one outside it", () => {
    // The Lab's rule verbatim: a pasted `?strategy=` and `?run=` that disagree
    // must still land somewhere rather than on an empty body.
    expect(pickRun(runs, "brl_mm", { kind: "experiment", number: 1 })?.run_id).toBe(
      "s2",
    );
  });

  it("is null for a scope with no runs", () => {
    expect(pickRun(runs, "nothing_here", null)).toBeNull();
  });
});
