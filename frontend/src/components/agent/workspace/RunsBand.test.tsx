/**
 * The Runs band on its own (ARCH-399): what a selected rail row opens.
 *
 * The host's tests assert the band is mounted behind its tab; these assert
 * what is inside it without mounting the screen — a session's ticks, a
 * conversation's ledger, the sentence that tells *deployed nothing* apart from
 * *ran before we recorded it*, and the delegation sheet a background task opens.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentRunRow } from "@/lib/api";

const getConversationDeployments = vi.fn();
const getSessionJournal = vi.fn();
const getSessionActions = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getConversationDeployments: (...a: unknown[]) =>
      getConversationDeployments(...a),
    getSessionJournal: (...a: unknown[]) => getSessionJournal(...a),
    getSessionActions: (...a: unknown[]) => getSessionActions(...a),
  },
}));

const mounted: string[] = [];
vi.mock("@/components/agent/lab/RunRail", () => ({
  RunRail: ({ selectedKey }: { selectedKey: string | null }) => {
    mounted.push("rail");
    return <div data-body="rail" data-selected={selectedKey ?? ""} />;
  },
}));
vi.mock("@/components/agent/lab/ExperimentDetail", () => ({
  ExperimentDetail: () => {
    mounted.push("experiment");
    return <div data-body="experiment" />;
  },
}));
vi.mock("@/components/agent/session/SessionCanvasPanel", () => ({
  SessionCanvasPanel: () => {
    mounted.push("canvas");
    return <div data-body="canvas" />;
  },
}));
/** What the market-chart stub was last handed (ARCH-427). */
let chartProps: Record<string, unknown> | null = null;
vi.mock("@/components/agent/session/SessionExecutors", () => ({
  SessionExecutors: (props: Record<string, unknown>) => {
    mounted.push("chart");
    chartProps = props;
    return <div data-body="chart" />;
  },
}));
/** The task the delegation stub was last handed. */
let delegationTask: Record<string, unknown> | null = null;
vi.mock("@/components/agent/DelegationSheet", () => ({
  DelegationSheet: ({ task }: { task: Record<string, unknown> }) => {
    mounted.push("delegation");
    delegationTask = task;
    return <div data-body="delegation" />;
  },
}));

const { RunsBand } = await import("./RunsBand");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SESSION = {
  id: "session_2",
  run_id: "s:2",
  kind: "session",
  number: 2,
  strategy_slug: "brl_mm",
  title: "",
  status: "done",
  started_at: 100,
  ended_at: 900,
  has_actions_log: true,
} as unknown as AgentRunRow;

const CHAT = {
  id: "7f3a",
  run_id: "c:7f3a",
  kind: "conversation",
  number: 0,
  strategy_slug: "",
  title: "Deploy a PMM on SOL",
  status: "done",
  started_at: 100,
  ended_at: 150,
} as unknown as AgentRunRow;

const DELEGATION = {
  id: "abc123",
  run_id: "d:abc123",
  kind: "delegation",
  number: 0,
  strategy_slug: "",
  title: "Check the BRL book",
  status: "running",
  execution_mode: "delegate",
  started_at: 300,
  ended_at: null,
} as unknown as AgentRunRow;

let container: HTMLDivElement;
let root: Root;

async function render(
  run: AgentRunRow | null,
  extra: Partial<Parameters<typeof RunsBand>[0]> = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <RunsBand
            slug="brigado"
            runs={run ? [run] : []}
            selectedRun={run}
            strategyFilter={null}
            onStrategyFilter={() => {}}
            onSelectRun={() => {}}
            onClearRun={() => {}}
            hasMore={false}
            onShowMore={() => {}}
            onOpenTick={() => {}}
            onShowNow={() => {}}
            serverName="local"
            controllerIds={["brigado_ctrl"]}
            {...extra}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mounted.length = 0;
  delegationTask = null;
  chartProps = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getConversationDeployments.mockReset();
  getSessionJournal.mockReset();
  getSessionActions.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("a loop session", () => {
  it("lists every tick newest first, with what each did, and opens one", async () => {
    getSessionJournal.mockResolvedValue({
      content: [
        "## Ticks",
        "- tick#1 | 2026-09-18 14:00 | actions=1 | Deployed 6 controllers",
        "- tick#2 | 2026-09-18 15:00 | actions=0 | Held; all PnL positive",
      ].join("\n"),
    });
    getSessionActions.mockResolvedValue({
      actions: [
        { tick: 1, at: 1, tool: "t", verb: "v", summary: "Deploy pmm_king", ok: true, error: "" },
      ],
    });
    const onOpenTick = vi.fn();
    await render(SESSION, { onOpenTick });
    expect(getSessionJournal).toHaveBeenCalledWith("brigado", "brl_mm", 2);
    const rows = [...container.querySelectorAll<HTMLButtonElement>("[data-run-tick]")];
    expect(rows.map((r) => r.dataset.runTick)).toEqual(["2", "1"]);
    expect(rows[0].textContent).toContain("Held; all PnL positive");
    expect(rows[1].textContent).toContain("Deploy pmm_king");
    await act(async () => rows[1].click());
    expect(onOpenTick).toHaveBeenCalledWith(SESSION, 1);
  });

  it("heads the ticks with the canvas and the market chart the Detail tab held (ARCH-427)", async () => {
    getSessionJournal.mockResolvedValue({ content: "" });
    getSessionActions.mockResolvedValue({ actions: [] });
    const onOpenTick = vi.fn();
    await render(SESSION, { onOpenTick });
    expect(mounted).toContain("canvas");
    expect(mounted).toContain("chart");
    // The charts only — Fleet has the table and the positions — for the
    // selected session, streamed from the agent's own server.
    expect(chartProps).toMatchObject({
      chartsOnly: true,
      slug: "brigado",
      sslug: "brl_mm",
      sessionNum: 2,
      serverName: "local",
      controllerIds: ["brigado_ctrl"],
      isLiveSession: false,
    });
    // A tick bubble on the chart opens that tick, as a tick row does.
    (chartProps!.onSnapshotClick as (tick: number) => void)(4);
    expect(onOpenTick).toHaveBeenCalledWith(SESSION, 4);
  });

  it("streams a live session's chart", async () => {
    getSessionJournal.mockResolvedValue({ content: "" });
    getSessionActions.mockResolvedValue({ actions: [] });
    await render({ ...SESSION, status: "running" } as AgentRunRow);
    expect(chartProps?.isLiveSession).toBe(true);
  });
});

describe("a conversation run", () => {
  it("says it ran before the ledger rather than that it deployed nothing", async () => {
    getConversationDeployments.mockResolvedValue({
      deployments: [],
      predates_ledger: true,
    });
    await render(CHAT);
    expect(getConversationDeployments).toHaveBeenCalledWith("7f3a");
    expect(container.textContent).toContain("before Condor recorded");
    expect(container.querySelector("table")).toBeNull();
  });

  it("draws the deployment ledger once the ledger covers it", async () => {
    getConversationDeployments.mockResolvedValue({
      deployments: [
        {
          kind: "controller",
          label: "pmm_1",
          detail: "binance·SOL-USDC",
          created_tick: null,
          started_at: 120,
          ended_at: null,
          live: true,
          pnl: 12,
          volume: 400,
          scope: "ctrl:pmm_1",
        },
      ],
      predates_ledger: false,
    });
    await render(CHAT);
    expect(container.textContent).not.toContain("before Condor recorded");
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.textContent).toContain("pmm_1");
    expect(
      container.querySelector("[data-body=rail]")?.getAttribute("data-selected"),
    ).toBe(":c:7f3a");
  });
});

describe("a delegation run", () => {
  it("mounts the delegation sheet with the row's listing", async () => {
    await render(DELEGATION);
    expect(mounted).toContain("delegation");
    expect(getConversationDeployments).not.toHaveBeenCalled();
    expect(delegationTask).toEqual({
      task_id: "abc123",
      agent: "brigado",
      task: "Check the BRL book",
      status: "running",
      kind: "delegate",
      started_at: 300,
    });
  });
});

describe("no run in scope (CORR-376)", () => {
  it("says the runs are outside the window when the strategy has sessions", async () => {
    const onShowOlderRuns = vi.fn();
    await render(null, { onShowOlderRuns });
    expect(container.textContent).not.toContain("This agent has no runs yet.");
    const more = container.querySelector<HTMLButtonElement>("[data-show-older-runs]")!;
    await act(async () => more.click());
    expect(onShowOlderRuns).toHaveBeenCalledTimes(1);
  });

  it("still says there are no runs when nothing is outside the window", async () => {
    await render(null);
    expect(container.textContent).toContain("This agent has no runs yet.");
    expect(container.querySelector("[data-show-older-runs]")).toBeNull();
  });
});
