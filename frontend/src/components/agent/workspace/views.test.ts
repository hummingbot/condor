/**
 * The URL is the whole state, so the rules that read it are the feature.
 *
 * Four pages collapsed onto one route (FEAT-103), and what used to be four
 * components' worth of internal state is now four query parameters. Every
 * promise the screen makes — that a scope, a run, a tick and a set of open
 * disclosures can each be pasted into a new tab and land on the same thing — is
 * a promise about this file, so it is pinned here rather than through a
 * rendered page.
 *
 * `?view=` is not among them any more (FEAT-119). There is no body to name, so
 * the parameter left the grammar and became a redirect table one module over —
 * see `sections.test.ts`, which is where the promise that every old address
 * still resolves is kept.
 */

import { describe, expect, it } from "vitest";

import type { AgentRunRow, StrategySummary } from "@/lib/api";
import {
  alertsFor,
  journalNamesDeploy,
  parseWorkspace,
  pickRun,
  pickStrategy,
  runsRedirect,
  strategyRedirect,
} from "./views";

function run(over: Partial<AgentRunRow> = {}): AgentRunRow {
  return {
    run_id: "s:1",
    kind: "session",
    id: "1",
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
    title: "",
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

describe("parsing the URL", () => {
  it("reads a scope, a run, a tick and the open disclosures at once", () => {
    expect(
      parseWorkspace("?open=runs.money&strategy=brl_mm&run=s3&tick=7"),
    ).toEqual({
      strategy: "brl_mm",
      run: { kind: "session", number: 3, id: "3" },
      tick: 7,
      open: "runs.money",
    });
  });

  it("leaves a selection out rather than inventing one", () => {
    // Every one of these is a real answer downstream: no strategy named means
    // "decide from the data", and no `?open=` means "whatever this browser had".
    expect(parseWorkspace("")).toEqual({
      strategy: null,
      run: null,
      tick: null,
      open: null,
    });
  });

  it("hands `?open=` on raw, unjudged", () => {
    // The ids are `sections.ts`' business and it drops the ones it does not
    // know; parsing them twice is how two answers to one question start.
    expect(parseWorkspace("?open=lab").open).toBe("lab");
    expect(parseWorkspace("?open=").open).toBe("");
  });

  it("refuses a run id and a tick that are not one", () => {
    expect(parseWorkspace("?run=session_3").run).toBeNull();
    expect(parseWorkspace("?tick=seven").tick).toBeNull();
    expect(parseWorkspace("?tick=-2").tick).toBeNull();
  });

  it("ignores a legacy `?view=` — the page answers that one with a redirect", () => {
    // Reading it here as well as there would be two surfaces deciding what an
    // old address means, and the page is the one that can navigate.
    expect(parseWorkspace("?view=money&tab=skills")).toEqual({
      strategy: null,
      run: null,
      tick: null,
      open: null,
    });
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
    expect(pickRun(runs, "brl_mm", { kind: "session", number: 1, id: "1" })?.run_id).toBe("s1");
  });

  it("is the newest in scope when the URL names none", () => {
    expect(pickRun(runs, "brl_mm", null)?.run_id).toBe("s2");
  });

  it("is the newest in scope when the URL names one outside it", () => {
    // The Lab's rule verbatim: a pasted `?strategy=` and `?run=` that disagree
    // must still land somewhere rather than on an empty body.
    expect(pickRun(runs, "brl_mm", { kind: "experiment", number: 1, id: "1" })?.run_id).toBe(
      "s2",
    );
  });

  it("is null for a scope with no runs", () => {
    expect(pickRun(runs, "nothing_here", null)).toBeNull();
  });

  it("resolves a chat or a task even while a strategy is in scope", () => {
    // The scope is a *strategy*, and those two kinds have none (FEAT-111) — so
    // scoping them out would make `?run=c:…` unopenable on any agent that also
    // loops, which is every agent the union exists for.
    const withChat = [
      ...runs,
      run({
        run_id: "c:7f3a",
        kind: "conversation",
        id: "7f3a",
        number: 0,
        strategy_slug: "",
        strategy_name: "",
        started_at: 500,
      }),
      run({
        run_id: "d:abc",
        kind: "delegation",
        id: "abc",
        number: 0,
        strategy_slug: "",
        strategy_name: "",
        started_at: 400,
      }),
    ];
    expect(
      pickRun(withChat, "brl_mm", { kind: "conversation", number: 0, id: "7f3a" })
        ?.run_id,
    ).toBe("c:7f3a");
    expect(
      pickRun(withChat, "brl_mm", { kind: "delegation", number: 0, id: "abc" })
        ?.run_id,
    ).toBe("d:abc");
  });

  it("never opens on a chat by default", () => {
    // A chat is addressable, not the default selection: a bare `/agents/:slug`
    // opens on the loop, which is what the rest of the screen is about.
    const chatFirst = [
      run({
        run_id: "c:new",
        kind: "conversation",
        id: "new",
        number: 0,
        strategy_slug: "",
        strategy_name: "",
        started_at: 9_000,
      }),
      run({ run_id: "s:2", id: "2", number: 2, started_at: 2_000 }),
    ];
    expect(pickRun(chatFirst, null, null)?.run_id).toBe("s:2");
  });
});

describe("the retired addresses still resolve", () => {
  it("sends the Lab's URL to the Runs disclosure, query string intact", () => {
    expect(runsRedirect("brigado", "?strategy=brl_mm&run=s3&tick=7")).toBe(
      "/agents/brigado?strategy=brl_mm&run=s3&tick=7&open=runs",
    );
    // And a bare one still lands somewhere.
    expect(runsRedirect("brigado", "")).toBe("/agents/brigado?open=runs");
  });

  it("drops a `?view=` it was carrying rather than passing a dead word on", () => {
    expect(runsRedirect("brigado", "?view=runs&strategy=brl_mm")).toBe(
      "/agents/brigado?strategy=brl_mm&open=runs",
    );
  });

  it("sends the strategy page's URL to the playbook, scoped to it", () => {
    expect(strategyRedirect("brigado", "brl_mm", "")).toBe(
      "/agents/brigado?open=playbook&strategy=brl_mm",
    );
  });

  it("round-trips: what the redirect writes is what the parser reads", () => {
    const there = runsRedirect("brigado", "?strategy=brl_mm&run=s3&tick=7");
    expect(parseWorkspace(there.slice(there.indexOf("?")))).toEqual({
      strategy: "brl_mm",
      run: { kind: "session", number: 3, id: "3" },
      tick: 7,
      open: "runs",
    });
  });

  it("escapes a slug that needs it", () => {
    expect(runsRedirect("my agent", "")).toBe("/agents/my%20agent?open=runs");
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
