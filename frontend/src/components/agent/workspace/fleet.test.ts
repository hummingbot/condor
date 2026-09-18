/**
 * The rules behind a fleet row, as the execution dock reads them.
 *
 * Pinned here: which loop a row is about, whether its money is a *statement*
 * or a fake zero, the order that puts the thing that can change while you read
 * it at the top, and the addresses a row links to.
 */

import { describe, expect, it } from "vitest";

import type { AgentSummary, RunningInstance, StrategySummary } from "@/lib/api";
import {
  attributedMoney,
  declaredServerOf,
  decisionHref,
  dueInSec,
  fleetAlerts,
  fleetRows,
  rowHref,
  scopeStrategy,
  tickCountdownLabel,
} from "./fleet";

function instance(over: Partial<RunningInstance> = {}): RunningInstance {
  return {
    agent_id: "brigado.brl_mm_1",
    status: "running",
    tick_count: 12,
    last_tick_at: 1_000,
    frequency_sec: 60,
    last_action: "",
    last_did: null,
    server_name: "brigado",
    ...over,
  } as RunningInstance;
}

function strategy(over: Partial<StrategySummary> = {}): StrategySummary {
  return {
    slug: "brl_mm",
    name: "BRL MM",
    session_count: 3,
    instances: [],
    ...over,
  } as StrategySummary;
}

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    slug: "brigado",
    name: "Brigado",
    agent_key: "claude-opus",
    status: "idle",
    session_count: 3,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    strategies: [],
    instances: [],
    ...over,
  } as AgentSummary;
}

describe("the strategy a row is about", () => {
  it("is the running loop, over a paused one and over an idle strategy", () => {
    const picked = scopeStrategy(
      agent({
        strategies: [
          strategy({ slug: "idle_one", instances: [] }),
          strategy({
            slug: "paused_one",
            instances: [instance({ status: "paused" })],
          }),
          strategy({ slug: "live_one", instances: [instance()] }),
        ],
      }),
    );
    expect(picked.strategy?.slug).toBe("live_one");
    expect(picked.live?.status).toBe("running");
  });

  it("falls back to a paused loop before an idle strategy", () => {
    const picked = scopeStrategy(
      agent({
        strategies: [
          strategy({ slug: "never", session_count: 0 }),
          strategy({
            slug: "paused_one",
            instances: [instance({ status: "paused" })],
          }),
        ],
      }),
    );
    expect(picked.strategy?.slug).toBe("paused_one");
  });

  it("falls back to a strategy that has actually run when nothing is live", () => {
    const picked = scopeStrategy(
      agent({
        strategies: [
          strategy({ slug: "never", session_count: 0 }),
          strategy({ slug: "ran_once", session_count: 1 }),
        ],
      }),
    );
    expect(picked.strategy?.slug).toBe("ran_once");
    expect(picked.live).toBeNull();
  });
});

describe("attributed money", () => {
  it("is a dash, not a zero, when nothing has been attributed", () => {
    expect(
      attributedMoney({ total_pnl: 0, total_volume: 0, open_positions: 0 }),
    ).toEqual({ net: null, volume: null });
  });

  it("prints a real zero when the agent actually traded to it", () => {
    // Volume means the ledger has something to say; a net of exactly zero is
    // then a fact, not an absence.
    expect(
      attributedMoney({ total_pnl: 0, total_volume: 4_200, open_positions: 0 }),
    ).toEqual({ net: 0, volume: 4_200 });
  });

  it("counts an open position as a statement even before any volume", () => {
    expect(
      attributedMoney({ total_pnl: 0, total_volume: 0, open_positions: 2 }).net,
    ).toBe(0);
  });

  it("reports a loss as a loss", () => {
    expect(
      attributedMoney({ total_pnl: -18.5, total_volume: 0, open_positions: 0 }),
    ).toEqual({ net: -18.5, volume: 0 });
  });
});

describe("the alerts", () => {
  it("reuse the workspace's rule for a failed deed", () => {
    const alerts = fleetAlerts(
      instance({
        last_did: {
          tick: 9,
          at: 0,
          tool: "manage_controllers",
          verb: "manage_controllers:upsert",
          summary: "Upsert controller pmm_1",
          ok: false,
          error: "boom",
        },
      }),
      1_000,
    );
    expect(alerts.map((a) => a.kind)).toEqual(["failed"]);
    expect(alerts[0].tick).toBe(9);
  });

  it("raise an overdue tick", () => {
    const alerts = fleetAlerts(instance(), 1_400);
    expect(alerts.map((a) => a.kind)).toEqual(["overdue"]);
    // Worded with the loop bar's own `countdown()`, not raw seconds (READ-424).
    expect(alerts[0].text).toBe("The next tick is overdue by 5m 40s.");
  });

  it("never raise the unledgered alarm, which this page cannot check", () => {
    // The journal and the deployment ledger are per-run reads the overview
    // deliberately does not make, so it must not claim to have compared them.
    expect(fleetAlerts(instance({ last_tick_at: 1_000 }), 1_010)).toEqual([]);
  });

  it("say nothing at all when nothing is looping", () => {
    expect(fleetAlerts(null, 9_999)).toEqual([]);
  });
});

describe("the next tick", () => {
  it("counts down, and goes negative once it is late", () => {
    expect(dueInSec(instance(), 1_020)).toBe(40);
    expect(dueInSec(instance(), 1_100)).toBe(-40);
  });

  it("is unknowable for a loop that has not ticked yet", () => {
    expect(dueInSec(instance({ last_tick_at: 0 }), 1_000)).toBeNull();
    expect(dueInSec(null, 1_000)).toBeNull();
  });

  it("is unknowable for a loop with no cadence, rather than forever overdue", () => {
    // Nothing constrains `frequency_sec` to be positive, and `last_tick_at -
    // now` on a 0-cadence loop is a large negative that reads as "overdue 4h"
    // on every surface that skips this guard.
    expect(dueInSec(instance({ frequency_sec: 0 }), 1_000)).toBeNull();
    expect(dueInSec(instance({ frequency_sec: -30 }), 1_000)).toBeNull();
  });

  it("is worded the same wherever it is printed", () => {
    expect(tickCountdownLabel(40)).toBe("next in 40s");
    expect(tickCountdownLabel(-40)).toBe("overdue 40s");
    // Due exactly now has already slipped, so it is overdue, not "next in 0s".
    expect(tickCountdownLabel(0)).toBe("overdue 0s");
  });
});

describe("the order of the rows", () => {
  const rows = () =>
    fleetRows(
      [
        agent({
          slug: "idle_rich",
          name: "Idle Rich",
          total_pnl: 500,
          total_volume: 10,
          strategies: [strategy()],
        }),
        agent({
          slug: "idle_silent",
          name: "Idle Silent",
          strategies: [strategy()],
        }),
        agent({
          slug: "looping_poor",
          name: "Looping Poor",
          status: "running",
          total_pnl: -20,
          total_volume: 10,
          strategies: [strategy({ instances: [instance()] })],
        }),
        agent({
          slug: "paused_one",
          name: "Paused One",
          total_pnl: 900,
          total_volume: 10,
          strategies: [strategy({ instances: [instance({ status: "paused" })] })],
        }),
      ],
      1_000,
    ).map((r) => r.slug);

  it("puts what is running first, whatever it has made", () => {
    // A loop trading unattended is the only thing on the page that can change
    // while it is being read — even down $20 against an idle agent's $900.
    expect(rows()[0]).toBe("looping_poor");
    expect(rows()[1]).toBe("paused_one");
  });

  it("ranks a real loss above a dash", () => {
    // Ranking "nothing to report" above a reported loss would be the fake zero
    // again, one level up.
    expect(rows().slice(2)).toEqual(["idle_rich", "idle_silent"]);
  });
});

describe("an agent with no strategies", () => {
  it("gets no row", () => {
    const agents = [agent({ slug: "bare" }), agent({ slug: "has", strategies: [strategy()] })];
    expect(fleetRows(agents, 0).map((r) => r.slug)).toEqual(["has"]);
  });

});

describe("the addresses a row carries", () => {
  const row = fleetRows(
    [
      agent({
        strategies: [
          strategy({
            instances: [
              instance({
                last_did: {
                  tick: 42,
                  at: 0,
                  tool: "manage_bots",
                  verb: "manage_bots:deploy",
                  summary: "Deploy brigado-brl_mm",
                  ok: true,
                  error: "",
                },
              }),
            ],
          }),
        ],
      }),
    ],
    1_000,
  )[0];

  it("open the workspace already scoped to the strategy in question", () => {
    expect(rowHref(row)).toBe("/agents/brigado?strategy=brl_mm");
  });

  it("make the last decision a link into the tick that made it", () => {
    expect(decisionHref(row)).toBe(
      "/agents/brigado?strategy=brl_mm&tick=42",
    );
  });

  it("fall back to the workspace when there is no deed to point at", () => {
    expect(decisionHref({ ...row, lastDid: null })).toBe(
      "/agents/brigado?strategy=brl_mm",
    );
  });
});

describe("which server a row declares (ARCH-324)", () => {
  it("prefers the strategy's own over the agent's pin", () => {
    expect(
      declaredServerOf(
        agent({ server_name: "the_pin" }),
        strategy({ server_name: "brigado" }),
      ),
    ).toBe("brigado");
  });

  it("falls back to the agent's pin, then to nothing", () => {
    expect(declaredServerOf(agent({ server_name: "the_pin" }), strategy())).toBe(
      "the_pin",
    );
    expect(declaredServerOf(agent(), strategy())).toBe("");
  });

});
