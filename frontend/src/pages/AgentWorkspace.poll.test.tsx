/**
 * What `/agents/:slug` costs while you read it (PERF-343).
 *
 * `GET /agents/{slug}` is the expensive read on this route: it builds a summary
 * per strategy and prices each one's sessions through the Hummingbot API.
 * PERF-305 established that the key must only poll while something is looping,
 * and gated it in `AgentStrategies` and `AgentKnowledge` — but react-query takes
 * the *shortest* interval declared among a key's observers, so the workspace's
 * two ungated `refetchInterval: 5000` declarations (the page's, for the header's
 * "Live" badge and the delete guard, and the screen's, for the strategy picker's
 * labels) overrode that gate on the surface a reader leaves open longest.
 *
 * So the assertion is a request count, taken through the real pair: this file
 * mounts the page, which mounts the screen, so both declarations are live on the
 * key at once and a gate on only one of them still fails. An idle agent is read
 * once and then left alone; a running one still refreshes every 5s, which is the
 * half that stops a component that never polls at all from passing.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentDetail } from "@/lib/api";

const getAgent = vi.fn(async () => DETAIL);

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: () => getAgent(),
    getAgentRuns: () => Promise.resolve([]),
    getStrategy: () => Promise.resolve(null),
    getConversationDeployments: () => Promise.resolve([]),
    getSessionJournal: () => Promise.resolve({ content: "" }),
    getSessionActions: () => Promise.resolve({ actions: [] }),
    getSessionReport: () => Promise.resolve({ report: null }),
    getStrategySessionExecutors: () => Promise.resolve({ executors: [] }),
  },
  CHAT_SLUG: "condor",
}));

// The disclosure bodies each fetch their own world and none of them is what is
// being counted here; the same stubs `AgentRunScreen.test.tsx` uses.
const stub = () => () => null;
vi.mock("@/components/agent/workspace/NowView", () => ({ NowView: stub() }));
vi.mock("@/components/agent/workspace/MoneyView", () => ({ MoneyView: stub() }));
vi.mock("@/components/agent/workspace/AgentFleet", () => ({
  AgentFleet: stub(),
}));
vi.mock("@/components/agent/workspace/PlaybookView", () => ({
  PlaybookView: stub(),
}));
vi.mock("@/components/agent/lab/RunRail", () => ({ RunRail: stub() }));
vi.mock("@/components/agent/lab/RunOverview", () => ({
  RunOverview: stub(),
  ExperimentDetail: stub(),
}));
vi.mock("@/components/agent/AgentSessionContent", () => ({
  SnapshotDetail: stub(),
}));
vi.mock("@/components/agent/DelegationSheet", () => ({
  DelegationSheet: stub(),
}));
// The header's own pickers fetch models and servers; the badge it draws from
// this key is covered where the key is invalidated, not here.
vi.mock("@/components/agent/workspace/WorkspaceHeader", () => ({
  WorkspaceHeader: () => null,
}));

const { AgentWorkspace } = await import("./AgentWorkspace");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let DETAIL: AgentDetail;

function detail(...statuses: string[]): AgentDetail {
  return {
    slug: "brigado",
    name: "Brigado",
    description: "",
    agent_md: "",
    agent_key: "claude-code",
    tools: [],
    when_to_consult: "",
    server_required: false,
    server_name: "brigado_2",
    strategies: statuses.map((status, i) => ({
      slug: `s${i}`,
      name: `Loop ${i}`,
      status,
      instances: [],
    })),
  } as unknown as AgentDetail;
}

let container: HTMLDivElement;
let root: Root;

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/agents/brigado"]}>
        <QueryClientProvider client={client}>
          <Routes>
            <Route path="/agents/:slug" element={<AgentWorkspace />} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
}

/** Let `ms` of polling elapse, flushing the fetches it schedules. */
async function elapse(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  getAgent.mockClear();
  DETAIL = detail("stopped");
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("the workspace's agent-detail poll", () => {
  it("reads an idle agent once and then stops asking", async () => {
    DETAIL = detail("stopped", "idle");
    mount();
    await elapse(0);
    expect(getAgent).toHaveBeenCalledTimes(1);

    // A minute of an open tab — twelve cadences — is still the one read.
    await elapse(60_000);
    expect(getAgent).toHaveBeenCalledTimes(1);
  });

  it("keeps the 5s cadence while a strategy is running", async () => {
    DETAIL = detail("stopped", "running");
    mount();
    await elapse(0);
    expect(getAgent).toHaveBeenCalledTimes(1);

    await elapse(5_000);
    expect(getAgent).toHaveBeenCalledTimes(2);

    await elapse(5_000);
    expect(getAgent).toHaveBeenCalledTimes(3);
  });

  it("goes quiet once the last loop stops", async () => {
    DETAIL = detail("running");
    mount();
    await elapse(0);

    DETAIL = detail("stopped");
    await elapse(5_000);
    const afterStop = getAgent.mock.calls.length;

    await elapse(60_000);
    expect(getAgent).toHaveBeenCalledTimes(afterStop);
  });
});
