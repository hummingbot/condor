import type { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import {
  invalidateLifecycle,
  invalidateOwnership,
  invalidateStrategyCatalog,
} from "@/components/agent/agentQueries";

function fakeClient() {
  const invalidateQueries = vi.fn();
  return {
    client: { invalidateQueries } as unknown as QueryClient,
    keys: () => invalidateQueries.mock.calls.map(([arg]) => arg),
  };
}

describe("agent invalidation sets", () => {
  it("invalidateLifecycle re-reads the strategy and the agent detail", () => {
    const { client, keys } = fakeClient();
    invalidateLifecycle(client, "brigado", "brl_mm");
    expect(keys()).toEqual([
      { queryKey: ["strategy", "brigado", "brl_mm"] },
      { queryKey: ["agent", "brigado"] },
    ]);
  });

  it("invalidateStrategyCatalog re-reads both catalogues that count strategies", () => {
    const { client, keys } = fakeClient();
    invalidateStrategyCatalog(client, "brigado");
    expect(keys()).toEqual([
      { queryKey: ["agent", "brigado"] },
      { queryKey: ["agent-brain", "brigado"] },
    ]);
  });

  it("invalidateOwnership re-reads the fleet map, the strategy and the agent", () => {
    const { client, keys } = fakeClient();
    invalidateOwnership(client, "brigado", "brl_mm");
    expect(keys()).toEqual([
      { queryKey: ["fleet-map"] },
      { queryKey: ["strategy", "brigado", "brl_mm"] },
      { queryKey: ["agent", "brigado"] },
    ]);
  });
});
