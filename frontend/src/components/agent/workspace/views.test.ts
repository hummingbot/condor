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
  alertsFor,
  journalNamesDeploy,
  WORKSPACE_VIEWS,
  isWorkspaceView,
  parseWorkspace,
  pickRun,
  pickStrategy,
  runsRedirect,
  spineSectionFor,
  strategyRedirect,
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

describe("the retired addresses still resolve", () => {
  it("sends the Lab's URL to the runs view with its query string intact", () => {
    expect(runsRedirect("brigado", "?strategy=brl_mm&run=s3&tick=7")).toBe(
      "/agents/brigado?strategy=brl_mm&run=s3&tick=7&view=runs",
    );
    // And a bare one still lands somewhere.
    expect(runsRedirect("brigado", "")).toBe("/agents/brigado?view=runs");
  });

  it("sends the strategy page's URL to the playbook, scoped to it", () => {
    expect(strategyRedirect("brigado", "brl_mm", "")).toBe(
      "/agents/brigado?view=playbook&strategy=brl_mm",
    );
  });

  it("round-trips: what the redirect writes is what the parser reads", () => {
    const there = runsRedirect("brigado", "?strategy=brl_mm&run=s3&tick=7");
    expect(parseWorkspace(there.slice(there.indexOf("?")))).toEqual({
      view: "runs",
      strategy: "brl_mm",
      run: { kind: "session", number: 3 },
      tick: 7,
    });
  });

  it("escapes a slug that needs it", () => {
    expect(runsRedirect("my agent", "")).toBe("/agents/my%20agent?view=runs");
  });
});

describe("what Now leads with", () => {
  const healthy = {
    actions: [{ tick: 4, ok: true, summary: "Create grid executor on SOL-USDC" }],
    deployments: 1,
    journalNamesDeploy: true,
    loop: { status: "running", last_tick_at: 1_000, frequency_sec: 60 },
    nowSec: 1_030,
  };

  it("raises nothing on a healthy live run", () => {
    // The rule that matters most: a loop doing its job must be silent, or the
    // count on the spine stops meaning anything.
    expect(alertsFor(healthy)).toEqual([]);
  });

  it("raises a failed action, and names the tick to open", () => {
    // Only knowable since FEAT-102 gave the action log its arguments back and
    // started recording controller writes with ok: false.
    const alerts = alertsFor({
      ...healthy,
      actions: [
        { tick: 3, ok: true, summary: "Deploy bot" },
        { tick: 4, ok: false, summary: "Upsert controller pmm_1" },
      ],
    });
    expect(alerts).toHaveLength(1);
    expect(alerts[0].kind).toBe("failed");
    expect(alerts[0].tick).toBe(4);
    expect(alerts[0].text).toContain("Upsert controller pmm_1");
  });

  it("names the newest failure and counts the rest, rather than listing forty", () => {
    const alerts = alertsFor({
      ...healthy,
      actions: [
        { tick: 2, ok: false, summary: "first" },
        { tick: 9, ok: false, summary: "last" },
      ],
    });
    expect(alerts).toHaveLength(1);
    expect(alerts[0].text).toContain("2 actions failed");
    expect(alerts[0].tick).toBe(9);
  });

  it("raises a deploy the ledger never recorded", () => {
    const alerts = alertsFor({ ...healthy, deployments: 0 });
    expect(alerts.map((a) => a.kind)).toEqual(["unledgered"]);
  });

  it("says nothing about an empty ledger when nothing claimed a deploy", () => {
    // Most runs deploy nothing, and for a research run that is the true answer
    // rather than a problem.
    expect(
      alertsFor({ ...healthy, deployments: 0, journalNamesDeploy: false }),
    ).toEqual([]);
  });

  it("raises an overdue tick", () => {
    const alerts = alertsFor({ ...healthy, nowSec: 1_090 });
    expect(alerts.map((a) => a.kind)).toEqual(["overdue"]);
    expect(alerts[0].text).toContain("30s overdue");
  });

  it("does not call a stopped loop overdue", () => {
    // A loop nobody started is not late; it is off, which the loop bar says.
    expect(
      alertsFor({
        ...healthy,
        nowSec: 900_000,
        loop: { status: "stopped", last_tick_at: 1_000, frequency_sec: 60 },
      }),
    ).toEqual([]);
    expect(alertsFor({ ...healthy, nowSec: 900_000, loop: null })).toEqual([]);
  });

  it("reads a deploy claim out of the agent's own words", () => {
    expect(
      journalNamesDeploy([{ action: "Deployed six controllers", reasoning: "" }]),
    ).toBe(true);
    expect(
      journalNamesDeploy([{ action: "Held", reasoning: "waiting to deploy later" }]),
    ).toBe(true);
    expect(journalNamesDeploy([{ action: "Held", reasoning: "spread too thin" }])).toBe(
      false,
    );
    expect(journalNamesDeploy([])).toBe(false);
  });
});
