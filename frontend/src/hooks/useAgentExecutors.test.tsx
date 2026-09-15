/**
 * That the agent's executor view rides the socket and only nets under it with
 * REST once a minute (PERF-336).
 *
 * The hook subscribes `executors:<server>` and then reads the *unfiltered*
 * executors key — the very key `shared-socket.ts` writes every frame into, at
 * the backend's 2s cadence. It nevertheless declared a 10s `refetchInterval` on
 * it, so an open agent session re-downloaded the whole newest-500 list six
 * times a minute to overwrite data two seconds old; and because react-query
 * drives a shared key at the shortest interval any observer asks for, it also
 * dragged Portfolio's deliberate 60s down to 10s.
 *
 * So what is pinned here is the request actually issued: how many times
 * `api.getExecutors` is called over three minutes of wall clock, and that a
 * socket write still reaches the caller with no request at all.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExecutorInfo } from "@/lib/api";

const getExecutors = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { getExecutors: (...args: unknown[]) => getExecutors(...args) },
}));

// The subscription itself is not under test — only the REST cadence beside it.
vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => undefined }));

const { executorsQuery } = await import("@/lib/queryClient");
const { useAgentExecutors } = await import("./useAgentExecutors");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SERVER = "srv";

/** An executor belonging to `controllerId`. */
function executor(id: string, controllerId: string): ExecutorInfo {
  return {
    id,
    type: "position_executor",
    connector: "binance",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "active",
    close_type: "",
    pnl: 0,
    volume: 0,
    timestamp: 0,
    controller_id: controllerId,
    cum_fees_quote: 0,
    net_pnl_pct: 0,
    entry_price: 0,
    current_price: 0,
    close_timestamp: 0,
    custom_info: {},
    config: {},
  } as ExecutorInfo;
}

/** Renders what the hook hands back, so the DOM is the record of it. */
function Harness() {
  const { executors } = useAgentExecutors(SERVER, ["ctrl-1"]);
  return <>{executors.map((e) => e.id).join(",")}</>;
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  getExecutors.mockReset().mockResolvedValue([executor("e1", "ctrl-1")]);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
  vi.useRealTimers();
});

async function mount() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

/** Runs `ms` of wall clock, letting every poll it schedules resolve. */
async function elapse(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("useAgentExecutors", () => {
  it("issues one unfiltered executors request per minute, not six", async () => {
    await mount();
    expect(getExecutors).toHaveBeenCalledTimes(1); // the initial read

    // Well past the old 10s cadence — which would have polled three times here.
    await elapse(59_000);
    expect(getExecutors).toHaveBeenCalledTimes(1);

    await elapse(2_000);
    expect(getExecutors).toHaveBeenCalledTimes(2);

    // Three minutes of an open agent session: three requests, not eighteen.
    await elapse(120_000);
    expect(getExecutors).toHaveBeenCalledTimes(4);
  });

  it("still shows a socket frame's executors without any request", async () => {
    await mount();
    expect(container.textContent).toBe("e1");
    const before = getExecutors.mock.calls.length;

    // What `shared-socket.ts` does with an `executors:<server>` frame.
    await act(async () => {
      client.setQueryData(executorsQuery(SERVER).queryKey, [
        executor("e1", "ctrl-1"),
        executor("e2", "ctrl-1"),
        executor("e3", "other-ctrl"),
      ]);
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(container.textContent).toBe("e1,e2");
    expect(getExecutors).toHaveBeenCalledTimes(before);
  });
});
