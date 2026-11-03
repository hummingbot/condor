/**
 * What the strategies grid costs while you look at it (PERF-305).
 *
 * The grid reads `["agent", slug]` because that is the only endpoint carrying
 * PnL, sessions and live instances — and the same endpoint prices every
 * session's executors through the Hummingbot API. On the agent page the page
 * itself polls that key anyway, but the chat's agent panel mounts this section
 * alone, so an unconditional 5s interval charged a Hummingbot round-trip per
 * session for an agent that was not running anything.
 *
 * So the load-bearing promise is the *pair*: an idle agent is read once and
 * then left alone, and a running one still refreshes on the 5s cadence the
 * cards were built for. Testing only the first half would pass on a component
 * that never polls at all.
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

import type { AgentDetail, StrategySummary } from "@/lib/api";
import { AgentStrategies } from "./AgentStrategies";

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: vi.fn(async () => {
      CALLS += 1;
      return DETAIL;
    }),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let CALLS = 0;
let DETAIL: AgentDetail;

// ── Fixtures ──

function strategy(status: string): StrategySummary {
  return {
    slug: `s-${status}`,
    name: `Loop ${status}`,
    description: "",
    status,
    agent_id: "a1",
    session_count: 1,
    experiment_count: 0,
    tick_count: 0,
    daily_pnl: 0,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    instances: [],
  };
}

function detail(strategies: StrategySummary[]): AgentDetail {
  return {
    slug: "scout",
    name: "Scout",
    description: "",
    agent_md: "",
    agent_key: "claude",
    tools: [],
    when_to_consult: "",
    server_required: false,
    server_name: "",
    strategies,
  };
}

let container: HTMLDivElement;
let root: Root;

/** The chat panel's host: this section alone, nothing else on the key. */
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AgentStrategies slug="scout" dense />
        </MemoryRouter>
      </QueryClientProvider>,
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
  CALLS = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("AgentStrategies polling", () => {
  it("reads an idle agent once and then stops asking", async () => {
    DETAIL = detail([strategy("stopped"), strategy("idle")]);
    mount();
    await elapse(0);
    expect(CALLS).toBe(1);

    // Four cadences' worth of an open pane: still the one read.
    await elapse(20_000);
    expect(CALLS).toBe(1);
  });

  it("keeps the 5s cadence while a strategy is running", async () => {
    DETAIL = detail([strategy("stopped"), strategy("running")]);
    mount();
    await elapse(0);
    expect(CALLS).toBe(1);

    await elapse(5_000);
    expect(CALLS).toBe(2);

    await elapse(5_000);
    expect(CALLS).toBe(3);
  });

  it("goes quiet once the last loop stops", async () => {
    DETAIL = detail([strategy("running")]);
    mount();
    await elapse(0);

    DETAIL = detail([strategy("stopped")]);
    await elapse(5_000);
    const afterStop = CALLS;

    await elapse(20_000);
    expect(CALLS).toBe(afterStop);
  });
});
