/**
 * That the pool-label query is keyed on *which* pools are wanted, never on the
 * order they happen to be ranked in (PERF-283).
 *
 * The positions the labels are fetched for are sorted by live quote value, so
 * two ranges of similar size trade places whenever the pool price moves. Keying
 * the label query on that order made every crossing look like a new set of
 * pools: a fresh GeckoTerminal call for addresses already in cache, and every
 * card title falling back to a truncated mint until it returned. So the two
 * cases worth pinning are a permutation (no fetch) against a genuinely
 * different set (exactly one fetch).
 *
 * Only `@/lib/api` is stubbed — the hook's own grouping and keying is what is
 * under test.
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
const getDexPoolsByAddress = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getExecutors: (...args: unknown[]) => getExecutors(...args),
    getDexPoolsByAddress: (...args: unknown[]) => getDexPoolsByAddress(...args),
  },
}));

const { lpPoolsKey, useLpPositions } = await import("./useLpPositions");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/** An open LP executor on `network`, holding `pool` and worth `value` in quote. */
function executor(id: string, network: string, pool: string, value: number): ExecutorInfo {
  return {
    id,
    type: "lp_executor",
    connector: network,
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "active",
    close_type: "",
    pnl: 0,
    volume: 0,
    timestamp: 0,
    controller_id: "",
    cum_fees_quote: 0,
    net_pnl_pct: 0,
    entry_price: 0,
    current_price: 0,
    close_timestamp: 0,
    custom_info: { total_value_quote: value },
    config: { pool_address: pool, connector_name: network },
  };
}

function Harness() {
  useLpPositions("srv");
  return null;
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let mounted = false;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  getExecutors.mockReset();
  getDexPoolsByAddress.mockReset().mockResolvedValue({ pools: [] });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mounted = false;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
});

async function settle() {
  await act(async () => {
    for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
  });
}

/**
 * Answers the next executors poll with `executors` and lets the two chained
 * queries settle. Mounting on the first call and invalidating on every later
 * one is what makes the second call a *poll* rather than a fresh page: the
 * executors query key never changes, so without the invalidation the hook would
 * keep reading the first answer out of cache and nothing would reorder.
 *
 * The label query cannot even be keyed until the executors query has answered,
 * so a bounded run of timer ticks is what "settled" means here.
 */
async function poll(executors: ExecutorInfo[]) {
  getExecutors.mockResolvedValue(executors);
  if (!mounted) {
    mounted = true;
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Harness />
        </QueryClientProvider>,
      );
    });
  } else {
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["dex-lp-executors"] });
    });
  }
  await settle();
  await settle();
}

describe("lpPoolsKey", () => {
  it("reads the same for the same pools in any order", () => {
    expect(lpPoolsKey({ solana: ["pool-b", "pool-a"] })).toBe(
      lpPoolsKey({ solana: ["pool-a", "pool-b"] }),
    );
    expect(lpPoolsKey({ solana: ["pool-a"], base: ["pool-c"] })).toBe(
      lpPoolsKey({ base: ["pool-c"], solana: ["pool-a"] }),
    );
  });

  it("reads differently for a different set of pools", () => {
    const one = lpPoolsKey({ solana: ["pool-a", "pool-b"] });
    expect(lpPoolsKey({ solana: ["pool-a"] })).not.toBe(one);
    expect(lpPoolsKey({ solana: ["pool-a", "pool-b", "pool-c"] })).not.toBe(one);
    // The same address on another chain is another pool.
    expect(lpPoolsKey({ base: ["pool-a", "pool-b"] })).not.toBe(one);
  });

  it("does not let one network's addresses bleed into another's", () => {
    expect(lpPoolsKey({ solana: ["a", "b"], base: [] })).not.toBe(
      lpPoolsKey({ solana: ["a"], base: ["b"] }),
    );
  });
});

describe("useLpPositions", () => {
  it("does not refetch labels when two positions cross in value", async () => {
    await poll([
      executor("1", "solana", "pool-a", 900),
      executor("2", "solana", "pool-b", 100),
    ]);
    expect(getDexPoolsByAddress).toHaveBeenCalledTimes(1);

    // Same two pools, the smaller one now the bigger — the ordinary effect of a
    // pool price moving between two 20s polls.
    await poll([
      executor("1", "solana", "pool-a", 100),
      executor("2", "solana", "pool-b", 900),
    ]);
    expect(getDexPoolsByAddress).toHaveBeenCalledTimes(1);
  });

  it("fetches again when a pool is added", async () => {
    await poll([executor("1", "solana", "pool-a", 900)]);
    expect(getDexPoolsByAddress).toHaveBeenCalledTimes(1);

    await poll([
      executor("1", "solana", "pool-a", 900),
      executor("2", "solana", "pool-b", 100),
    ]);
    expect(getDexPoolsByAddress).toHaveBeenCalledTimes(2);
  });

  it("fetches again when the same pool moves to another network", async () => {
    await poll([executor("1", "solana", "pool-a", 900)]);
    expect(getDexPoolsByAddress).toHaveBeenCalledTimes(1);

    await poll([executor("1", "base", "pool-a", 900)]);
    expect(getDexPoolsByAddress).toHaveBeenCalledTimes(2);
  });
});
