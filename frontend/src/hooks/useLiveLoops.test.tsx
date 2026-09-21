/**
 * "Every active loop" (FEAT-123) means filtered and sorted, not just fetched.
 *
 * `fleet-map` answers every owner whether or not it is looping; the hook's
 * whole job is picking the ones with a live loop out of that and putting them
 * in a stable order — running before paused, then by name — so the rail badge
 * and the panel agree on a count and a list a poll cannot reshuffle for no
 * reason.
 *
 * Only `@/lib/api` is stubbed. Needs a DOM for the query client's effects, so
 * this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FleetOwner, LiveLoop } from "@/lib/agent-attribution";

const getFleetMap = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { getFleetMap: () => getFleetMap() },
}));

const { useLiveLoops } = await import("./useLiveLoops");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function live(over: Partial<LiveLoop> = {}): LiveLoop {
  return {
    agentId: "a1",
    sessionNum: 1,
    status: "running",
    tickCount: 3,
    lastTickAt: 0,
    frequencySec: 30,
    lastAction: "",
    lastDid: null,
    lastError: "",
    ...over,
  };
}

function owner(over: Partial<FleetOwner> = {}): FleetOwner {
  return {
    runKey: "agent.strategy",
    agentSlug: "agent",
    agentName: "Agent",
    strategySlug: "strategy",
    strategyName: "Strategy",
    namespace: "agent-strategy",
    declaredBots: [],
    agentIds: [],
    live: null,
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

/** Renders as `slug/slug` per loop, in the order the hook returns them. */
function Harness() {
  const { loops } = useLiveLoops();
  return (
    <div data-testid="loops">
      {loops.map((o) => `${o.agentSlug}/${o.strategySlug}`).join(",")}
    </div>
  );
}

const seen = () =>
  (container.querySelector('[data-testid="loops"]')!.textContent || "")
    .split(",")
    .filter(Boolean);

async function renderWith(owners: FleetOwner[]) {
  getFleetMap.mockResolvedValue({ owners, deeds: { bots: {}, since: 0 } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  getFleetMap.mockReset();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
});

describe("useLiveLoops", () => {
  it("keeps only owners with a live loop, running before paused, by name", async () => {
    await renderWith([
      owner({
        agentSlug: "zeta",
        agentName: "Zeta",
        live: live({ status: "paused" }),
      }),
      owner({ agentSlug: "idle", agentName: "Idle", live: null }),
      owner({
        agentSlug: "alpha",
        agentName: "Alpha",
        live: live({ status: "running" }),
      }),
    ]);

    expect(seen()).toHaveLength(2);
    expect(seen()).toEqual(["alpha/strategy", "zeta/strategy"]);
  });

  it("orders two running loops by agent name, then strategy name", async () => {
    await renderWith([
      owner({
        agentSlug: "brigado",
        agentName: "Brigado",
        strategySlug: "z",
        strategyName: "Z",
        live: live(),
      }),
      owner({
        agentSlug: "brigado",
        agentName: "Brigado",
        strategySlug: "a",
        strategyName: "A",
        live: live(),
      }),
    ]);

    expect(seen()).toEqual(["brigado/a", "brigado/z"]);
  });
});
