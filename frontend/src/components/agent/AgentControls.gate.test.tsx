/**
 * What a lifecycle control makes stale (PERF-343).
 *
 * Gating `["agent", slug]` on "some strategy is running" only works if starting
 * one re-opens the gate. The controls used to invalidate `["strategy", …]`
 * alone, which was enough while the agent key polled unconditionally and is not
 * enough now: an idle agent's gate is closed, so nothing would ever ask again to
 * learn that the loop the reader just started is running — the "Live" badge and
 * the delete guard would stay wrong until a reload.
 *
 * So this pins the pair. A gated observer of the key sits beside the real
 * controls; resuming a paused loop must both re-read the key immediately and
 * leave the 5s cadence running afterwards.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentDetail } from "@/lib/api";

const getAgent = vi.fn(async () => DETAIL);
const resumeStrategy = vi.fn(async () => ({}));

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: () => getAgent(),
    getServers: () => Promise.resolve([]),
    resumeStrategy: (...a: unknown[]) => resumeStrategy(...(a as [])),
    pauseStrategy: () => Promise.resolve({}),
    stopStrategy: () => Promise.resolve({}),
    startStrategy: () => Promise.resolve({}),
  },
}));

const { AgentControls } = await import("./AgentControls");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let DETAIL: AgentDetail;

function detail(status: string): AgentDetail {
  return {
    slug: "brigado",
    name: "Brigado",
    description: "",
    agent_md: "",
    agent_key: "claude-code",
    tools: [],
    when_to_consult: "",
    server_required: false,
    server_name: "",
    strategies: [{ slug: "brl_mm", name: "BRL MM", status, instances: [] }],
  } as unknown as AgentDetail;
}

/** The gate every observer of this key now declares (PERF-305/PERF-343). */
function GatedReader() {
  useQuery({
    queryKey: ["agent", "brigado"],
    queryFn: () => getAgent(),
    refetchInterval: (q) =>
      (q.state.data as AgentDetail | undefined)?.strategies.some(
        (s) => s.status === "running",
      )
        ? 5000
        : false,
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <GatedReader />
        <AgentControls
          slug="brigado"
          sslug="brl_mm"
          status="paused"
          defaultContext=""
          agentConfig={{}}
        />
      </QueryClientProvider>,
    );
  });
}

async function elapse(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  getAgent.mockClear();
  resumeStrategy.mockClear();
  DETAIL = detail("paused");
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("resuming a loop", () => {
  it("re-reads the agent detail and re-arms its gated poll", async () => {
    mount();
    await elapse(0);
    expect(getAgent).toHaveBeenCalledTimes(1);

    // Idle: the gate is shut, so nothing but the control can reopen it.
    await elapse(30_000);
    expect(getAgent).toHaveBeenCalledTimes(1);

    DETAIL = detail("running");
    const resume = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Resume"),
    );
    expect(resume).toBeTruthy();
    await act(async () => {
      resume!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await elapse(0);

    expect(resumeStrategy).toHaveBeenCalledTimes(1);
    // The invalidation, not a poll: no cadence has elapsed.
    expect(getAgent).toHaveBeenCalledTimes(2);

    // And the gate is open again on what it read back.
    await elapse(5_000);
    expect(getAgent).toHaveBeenCalledTimes(3);
  });
});
