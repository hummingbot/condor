/**
 * The Execution panel's fleet, read agent-first (FEAT-114).
 *
 * `/fleet` is gone and its question — *what is every agent doing* — is a level
 * of this panel's tree. The four things that has to keep true are pinned here,
 * because all four are silent when they break: an agent row is a *partition* of
 * the panel's total rather than a selection from it, the bot level disappears
 * when it distinguishes nothing, nothing in the payload is dropped for having
 * no owner, and a run key nobody claims stays named instead of being swept into
 * a bucket with the genuinely unowned.
 */

import { describe, expect, it } from "vitest";

import {
  AUTO_OPEN_AGENTS,
  executionCounts,
  executionRows,
  openRows,
  visibleRows,
} from "./executionTree";
import type { AgentSummary, ControllerInfo } from "@/lib/api";
import type { ConvertQuote, PerfLeaf } from "@/lib/perf-tree";

const NOW = Date.parse("2026-09-04T12:00:00Z");

/** Display currency is the quote here: what is under test is the tree, not FX. */
const convert: ConvertQuote = (value) => value;

function leaf(over: Partial<PerfLeaf> & Pick<PerfLeaf, "id">): PerfLeaf {
  return {
    kind: "controller",
    label: over.id,
    bot: "bot-a",
    agent: "",
    how: "none",
    controllerId: over.id,
    executorType: "pmm_simple",
    connector: "backpack",
    pair: "SOL-USDC",
    realized: 0,
    unrealized: 0,
    net: 0,
    volume: 0,
    fees: 0,
    capital: 0,
    closeTypes: {},
    positions: [],
    startedAt: NOW - 3_600_000,
    endedAt: null,
    running: true,
    status: "running",
    source: {} as ControllerInfo,
    ...over,
  };
}

function agent(slug: string, name: string): AgentSummary {
  return {
    slug,
    name,
    status: "idle",
    session_count: 1,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    strategies: [],
  } as unknown as AgentSummary;
}

const rowsOf = (leaves: PerfLeaf[], agents: AgentSummary[] = []) =>
  executionRows({ leaves, deeds: null, agents, convert, now: NOW });

/** Everything on screen with every row expanded — what the assertions read. */
const allOpen = (leaves: PerfLeaf[], agents: AgentSummary[] = []) => {
  const rows = rowsOf(leaves, agents);
  return visibleRows(rows, new Set(rows.map((r) => r.id)));
};

describe("the agent rows", () => {
  it("partition the panel's total: every leaf lands under exactly one owner", () => {
    const leaves = [
      leaf({ id: "c1", bot: "brigado-1", agent: "brigado.brl_mm", how: "namespace", net: 120 }),
      leaf({ id: "c2", bot: "brigado-1", agent: "brigado.brl_mm", how: "namespace", net: -20 }),
      leaf({ id: "c3", bot: "quiet-1", agent: "quiet.sol_mm", how: "namespace", net: 7 }),
      // Nobody's: it still gets a row rather than falling out of the total.
      leaf({ id: "c4", bot: "handrolled-1", net: 3 }),
    ];

    const rows = rowsOf(leaves);
    const agentRows = rows.filter((r) => r.kind === "agent");

    expect(agentRows.map((r) => r.id)).toEqual([
      "agent:brigado.brl_mm",
      "agent:quiet.sol_mm",
      // The unowned bucket sinks below the named runs, as it does in the sidebar.
      "agent:" + " pre",
    ]);
    // The whole point: the rows add up to the fleet, so the panel's header and
    // its rows can never tell two different stories.
    const summed = agentRows.reduce((n, row) => n + row.totals.net, 0);
    expect(summed).toBe(110);
    expect(agentRows.map((r) => r.totals.net)).toEqual([100, 7, 3]);
    // And no leaf is counted twice on the way there.
    expect(agentRows.reduce((n, row) => n + row.leaves.length, 0)).toBe(4);
  });

  it("names an agent the fleet map claims, and links it by slug", () => {
    const leaves = [leaf({ id: "c1", bot: "brigado-1", agent: "brigado.brl_mm", how: "namespace" })];
    const [row] = allOpen(leaves, [agent("brigado", "Brigado")]);

    expect(row.kind).toBe("agent");
    expect(row.agent).toEqual({ slug: "brigado", name: "Brigado" });
    expect(row.label).toBe("Brigado / brl_mm");
  });

  it("keeps a residual run key named, and gives it no slug to open", () => {
    // Attributed — so it is in neither unowned bucket — but no listed agent
    // answers to it. Sweeping it away would lose money out of the total; a row
    // with a dead link would send the reader to a page that does not exist.
    const leaves = [leaf({ id: "c1", agent: "retired.old_mm", how: "deed", net: 5 })];
    const [row] = allOpen(leaves, [agent("brigado", "Brigado")]);

    expect(row.agent).toBeUndefined();
    expect(row.label).toBe("retired / old_mm");
    expect(row.totals.net).toBe(5);
  });

  it("files what nobody owns under a named bucket rather than dropping it", () => {
    const started = leaf({ id: "c1", bot: "outside-1", startedAt: NOW, net: 11 });
    const ancient = leaf({ id: "c2", bot: "old-1", startedAt: null, net: 2 });
    // `since` is epoch seconds: anything newer than it and unattributed was
    // made by something that is not Condor.
    const deeds = { since: Math.floor((NOW - 86_400_000) / 1000), byBot: {}, byController: {} };

    const rows = executionRows({
      leaves: [started, ancient],
      deeds: deeds as never,
      agents: [],
      convert,
      now: NOW,
    });
    const labels = rows.filter((r) => r.kind === "agent").map((r) => r.label);

    expect(labels).toEqual(["Outside Condor", "Before the ledger"]);
  });
});

describe("the bot level", () => {
  it("is not drawn for an agent running a single bot", () => {
    const leaves = [
      leaf({ id: "c1", bot: "brigado-1", agent: "brigado.brl_mm", how: "namespace" }),
      leaf({ id: "c2", bot: "brigado-1", agent: "brigado.brl_mm", how: "namespace" }),
    ];

    const rows = allOpen(leaves);
    expect(rows.map((r) => r.kind)).toEqual(["agent", "controller", "controller"]);
    // The controllers rise a level with it, so nothing is buried by the row
    // that was not worth drawing.
    expect(rows.slice(1).every((r) => r.depth === 1)).toBe(true);
    expect(rows.slice(1).every((r) => r.parentId === "agent:brigado.brl_mm")).toBe(true);
  });

  it("is drawn as soon as one agent runs two of them", () => {
    const leaves = [
      leaf({ id: "c1", bot: "brigado-1", agent: "brigado.brl_mm", how: "namespace", net: 4 }),
      leaf({ id: "c2", bot: "brigado-2", agent: "brigado.brl_mm", how: "namespace", net: 6 }),
    ];

    const rows = allOpen(leaves);
    expect(rows.map((r) => [r.kind, r.depth])).toEqual([
      ["agent", 0],
      ["bot", 1],
      ["controller", 2],
      ["bot", 1],
      ["controller", 2],
    ]);
    // A bot row folds its own branch, and the two still add up to the agent.
    const bots = rows.filter((r) => r.kind === "bot");
    expect(bots.map((r) => r.totals.net)).toEqual([4, 6]);
    expect(rows[0].totals.net).toBe(10);
  });

  it("collapses for one agent and not for another in the same fleet", () => {
    const leaves = [
      leaf({ id: "c1", bot: "brigado-1", agent: "brigado.brl_mm", how: "namespace" }),
      leaf({ id: "c2", bot: "brigado-2", agent: "brigado.brl_mm", how: "namespace" }),
      leaf({ id: "c3", bot: "quiet-1", agent: "quiet.sol_mm", how: "namespace" }),
    ];

    const rows = allOpen(leaves);
    expect(rows.filter((r) => r.kind === "bot").map((r) => r.label)).toEqual([
      "brigado-1",
      "brigado-2",
    ]);
  });
});

describe("what is open on arrival", () => {
  it("opens a small fleet's agents and leaves a big one's shut", () => {
    const small = rowsOf([
      leaf({ id: "c1", bot: "b1", agent: "a.one", how: "namespace" }),
      leaf({ id: "c2", bot: "b2", agent: "b.two", how: "namespace" }),
    ]);
    expect(visibleRows(small, openRows(small, {})).map((r) => r.kind)).toEqual([
      "agent",
      "controller",
      "agent",
      "controller",
    ]);

    const many = rowsOf(
      Array.from({ length: AUTO_OPEN_AGENTS + 1 }, (_, i) =>
        leaf({ id: `c${i}`, bot: `b${i}`, agent: `a${i}.s`, how: "namespace" }),
      ),
    );
    const shownKinds = visibleRows(many, openRows(many, {})).map((r) => r.kind);
    expect(new Set(shownKinds)).toEqual(new Set(["agent"]));
  });

  it("lets the reader open one of them, and shut one that was open", () => {
    const rows = rowsOf(
      Array.from({ length: AUTO_OPEN_AGENTS + 1 }, (_, i) =>
        leaf({ id: `c${i}`, bot: `b${i}`, agent: `a${i}.s`, how: "namespace" }),
      ),
    );
    const opened = visibleRows(rows, openRows(rows, { "agent:a0.s": true }));
    expect(opened.filter((r) => r.kind === "controller").map((r) => r.id)).toEqual([
      "ctrl:b0:c0",
    ]);

    const small = rowsOf([leaf({ id: "c1", bot: "b1", agent: "a.one", how: "namespace" })]);
    const shut = visibleRows(small, openRows(small, { "agent:a.one": false }));
    expect(shut.map((r) => r.kind)).toEqual(["agent"]);
  });

  it("gives a childless row no chevron to click", () => {
    // An agent whose only records are loose executors has nothing to expand
    // into: executors are counted on a controller, and there is no controller.
    const rows = rowsOf([
      leaf({ id: "e1", kind: "executor", controllerId: "", bot: "main", agent: "a.one", how: "deed" }),
    ]);
    expect(rows.map((r) => [r.kind, r.hasChildren])).toEqual([["agent", false]]);
    expect(openRows(rows, {}).size).toBe(0);
  });
});

describe("the header's counts", () => {
  it("counts the controllers and the paused ones, open or not", () => {
    const rows = rowsOf([
      leaf({ id: "c1", bot: "b1", agent: "a.one", how: "namespace" }),
      leaf({ id: "c2", bot: "b1", agent: "a.one", how: "namespace", status: "stopped" }),
      leaf({ id: "c3", bot: "b2", agent: "b.two", how: "namespace" }),
      // Executors are not controllers, whatever they are hanging under.
      leaf({ id: "e1", kind: "executor", controllerId: "c1", bot: "b1", agent: "a.one", how: "namespace" }),
    ]);

    expect(executionCounts(rows)).toEqual({ controllers: 3, paused: 1 });
  });
});
