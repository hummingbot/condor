/**
 * The rules that decide whether this page deserves to exist.
 *
 * A fleet card grid stood at `/` and was deleted for saying only which agents
 * are running. What is pinned here is everything the deleted grid could not
 * say: which loop a row is about, whether its money is a *statement* or a fake
 * zero, and the order that puts the thing that can change while you read it at
 * the top.
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
  foldServerOf,
  foldTargets,
  moneyHref,
  rowHref,
  scopeStrategy,
  strategylessAgents,
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

  it("is listed once by name rather than hidden", () => {
    const agents = [
      agent({ slug: "zeta", name: "Zeta" }),
      agent({ slug: "has", strategies: [strategy()] }),
      agent({ slug: "alpha", name: "Alpha" }),
    ];
    expect(strategylessAgents(agents).map((a) => a.slug)).toEqual([
      "alpha",
      "zeta",
    ]);
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

describe("the money column is named, not bare (FEAT-109)", () => {
  const row = fleetRows(
    [agent({ slug: "brigado", strategies: [strategy({ slug: "brl_mm" })] })],
    1_000,
  )[0];

  it("links the rollup to the screen that reconciles it against the fold", () => {
    expect(moneyHref(row)).toBe("/agents/brigado?open=money&strategy=brl_mm");
  });

  it("still has an address for an agent that owns no strategy", () => {
    expect(moneyHref({ ...row, strategy: null })).toBe("/agents/brigado?open=money");
  });
});

describe("which server a row's records are folded from (ARCH-324)", () => {
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

  it("takes the ambient server only when nobody declared one", () => {
    expect(foldServerOf({ declaredServer: "brigado" }, "ambient")).toBe("brigado");
    expect(foldServerOf({ declaredServer: "" }, "ambient")).toBe("ambient");
  });

  it("is empty — not a substitute — when there is no ambient one either", () => {
    expect(foldServerOf({ declaredServer: "" }, null)).toBe("");
  });
});

describe("what each server is asked to fold (ARCH-324)", () => {
  it("groups the rows by the server their records are fetched from", () => {
    expect(
      foldTargets(
        [
          agent({ slug: "a", server_name: "one", strategies: [strategy()] }),
          agent({ slug: "b", server_name: "two", strategies: [strategy()] }),
          agent({ slug: "c", server_name: "one", strategies: [strategy()] }),
        ],
        null,
      ),
    ).toEqual([
      {
        server: "one",
        targets: [
          { slug: "a", strategy: "brl_mm" },
          { slug: "c", strategy: "brl_mm" },
        ],
      },
      { server: "two", targets: [{ slug: "b", strategy: "brl_mm" }] },
    ]);
  });

  it("narrows each fold to the strategy its money link opens", () => {
    const [{ targets }] = foldTargets(
      [
        agent({
          server_name: "one",
          strategies: [strategy({ slug: "ema" }), strategy({ slug: "brl_mm" })],
        }),
      ],
      null,
    );
    // `scopeStrategy` picked it, `moneyHref` links to it, the fold narrows to
    // it — one scope, so the row and the headline cannot disagree.
    expect(targets).toEqual([{ slug: "brigado", strategy: "ema" }]);
  });

  it("leaves out an agent nobody has given a server, rather than guessing", () => {
    expect(foldTargets([agent({ strategies: [strategy()] })], null)).toEqual([]);
  });

  it("leaves out an agent that owns no strategy at all", () => {
    expect(foldTargets([agent({ server_name: "one" })], null)).toEqual([]);
  });
});
