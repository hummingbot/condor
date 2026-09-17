/**
 * The Runs band on its own (ARCH-399): what a selected rail row opens.
 *
 * The host's tests assert the band is mounted behind its disclosure; these
 * assert what is inside it without mounting the screen — a conversation's
 * ledger, the sentence that tells *deployed nothing* apart from *ran before we
 * recorded it*, and the delegation sheet a background task opens.
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

vi.mock("@/lib/api", () => ({
  api: {
    getConversationDeployments: (...a: unknown[]) =>
      getConversationDeployments(...a),
  },
}));

const mounted: string[] = [];
vi.mock("@/components/agent/lab/RunRail", () => ({
  RunRail: ({ selectedKey }: { selectedKey: string | null }) => {
    mounted.push("rail");
    return <div data-body="rail" data-selected={selectedKey ?? ""} />;
  },
}));
vi.mock("@/components/agent/lab/RunOverview", () => ({
  ExperimentDetail: () => {
    mounted.push("experiment");
    return <div data-body="experiment" />;
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

async function render(run: AgentRunRow) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <RunsBand
            slug="brigado"
            runs={[run]}
            selectedRun={run}
            strategyFilter={null}
            onStrategyFilter={() => {}}
            onSelectRun={() => {}}
            onClearRun={() => {}}
            hasMore={false}
            onShowMore={() => {}}
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
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getConversationDeployments.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
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
