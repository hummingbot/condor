/**
 * A session whose server was withheld says so, instead of reading as one that
 * traded nothing (CORR-430). The backend names the reason in `unavailable`
 * (CORR-706); `""` must render exactly as before.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { unavailableLabel } from "@/lib/strategy-unavailable";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const getStrategySessionExecutors = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: {
    getStrategySessionExecutors: (...a: unknown[]) => getStrategySessionExecutors(...a),
    getSessionSnapshots: vi.fn(async () => ({ snapshots: [] })),
    getPositionsHeld: vi.fn(async () => ({ positions: [], summary: {} })),
  },
}));
vi.mock("@/hooks/useAgentExecutors", () => ({
  useAgentExecutors: () => ({ executors: [] }),
}));
vi.mock("@/hooks/useSnapshotBubbles", () => ({
  useSnapshotBubbles: () => [],
}));
vi.mock("@/hooks/useRates", () => ({
  useRates: () => ({
    rates: {},
    currency: "USD",
    formatPnlValue: (v: number) => `$${v}`,
    formatValue: (v: number) => `$${v}`,
    formatValueDetailed: (v: number) => `$${v}`,
  }),
}));
vi.mock("@/components/charts/ExecutorChart", () => ({
  ExecutorChart: () => null,
}));

const { SessionExecutors } = await import("./SessionExecutors");

let container: HTMLDivElement;
let root: Root;

/** The route's no-client payload: zero money, no rows, and a reason. */
function payload(unavailable: string) {
  return {
    executors: [],
    performance: { agent_id: "brigado_brl_mm_3", session_num: 3 },
    pnl_series: [],
    deployments: [],
    unavailable,
  };
}

async function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SessionExecutors slug="brigado" sslug="brl_mm" sessionNum={3} serverName="local" />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // Let the query resolve and the component re-render with its data.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

const text = () => container.textContent ?? "";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getStrategySessionExecutors.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("SessionExecutors with a withheld server", () => {
  it('names "no_access" instead of the empty state, and prints no $0', async () => {
    getStrategySessionExecutors.mockResolvedValue(payload("no_access"));
    await mount();

    const note = container.querySelector("[data-session-unavailable]");
    expect(note?.textContent).toBe("Server access unavailable for this strategy");
    expect(text()).not.toContain("No executors for this session.");
    expect(text()).not.toContain("$0");
  });

  it('renders exactly as before when "unavailable" is ""', async () => {
    getStrategySessionExecutors.mockResolvedValue(payload(""));
    await mount();

    expect(container.querySelector("[data-session-unavailable]")).toBeNull();
    expect(text()).toBe("No executors for this session.");
  });

  it("treats an older backend that omits the field as available", async () => {
    const { unavailable: _omit, ...older } = payload("");
    void _omit;
    getStrategySessionExecutors.mockResolvedValue(older);
    await mount();

    expect(container.querySelector("[data-session-unavailable]")).toBeNull();
    expect(text()).toBe("No executors for this session.");
  });
});

describe("unavailableLabel", () => {
  it("has one line per reason and nothing for the priced state", () => {
    expect(unavailableLabel("no_access")).toBe("Server access unavailable for this strategy");
    expect(unavailableLabel("unreachable")).toBe("Server unreachable");
    expect(unavailableLabel("no_server")).toBe("No server configured");
    expect(unavailableLabel("")).toBe("");
    expect(unavailableLabel(undefined)).toBe("");
  });
});
