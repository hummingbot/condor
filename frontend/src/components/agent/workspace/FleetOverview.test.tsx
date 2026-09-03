/**
 * The overview has to beat the grid that was deleted from this address.
 *
 * `Agents.tsx`'s old docstring is the standard: a page whose only unique job is
 * showing which agents are running does not earn a place, because the rail's
 * live line already does that. So what is pinned here is the three things the
 * line cannot say — attributed money, the last decision as an address, and the
 * next tick — plus the one honesty rule that separates this from the grid's
 * numbers: a run that has claimed nothing shows a dash, not `$0.00`.
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

import type { AgentSummary, RunningInstance, StrategySummary } from "@/lib/api";
import { FleetOverview } from "./FleetOverview";

const getAgents = vi.fn<() => Promise<AgentSummary[]>>();

vi.mock("@/lib/api", () => ({
  api: { getAgents: () => getAgents() },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function instance(over: Partial<RunningInstance> = {}): RunningInstance {
  return {
    agent_id: "brigado.brl_mm_1",
    status: "running",
    tick_count: 42,
    last_tick_at: 1_000,
    frequency_sec: 60,
    last_action: "",
    last_did: null,
    server_name: "brigado",
    ...over,
  } as RunningInstance;
}

function strategy(over: Partial<StrategySummary> = {}): StrategySummary {
  return {
    slug: "brl_mm",
    name: "BRL MM",
    session_count: 3,
    instances: [],
    ...over,
  } as StrategySummary;
}

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    slug: "brigado",
    name: "Brigado",
    agent_key: "claude-opus",
    status: "idle",
    session_count: 3,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    strategies: [],
    instances: [],
    ...over,
  } as AgentSummary;
}

async function render(agents: AgentSummary[]) {
  getAgents.mockResolvedValue(agents);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <FleetOverview />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  // A second flush, and a real turn of the loop rather than a microtask:
  // react-query settles the fetch on the first and only paints what it
  // returned on the next tick. Without this every assertion below reads an
  // empty list and the failure looks like a render bug.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const text = () => container.textContent ?? "";
const rows = () =>
  [...container.querySelectorAll<HTMLElement>("[data-fleet-row]")];
const pick = (slug: string) =>
  container.querySelector<HTMLElement>(`[data-fleet-row="${slug}"]`)!;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  getAgents.mockReset();
  vi.useRealTimers();
});

describe("the order", () => {
  it("is running first, then by what has actually been attributed", async () => {
    await render([
      agent({
        slug: "idle_rich",
        name: "Idle Rich",
        total_pnl: 900,
        total_volume: 10,
        strategies: [strategy()],
      }),
      agent({
        slug: "looping",
        name: "Looping",
        status: "running",
        total_pnl: -20,
        total_volume: 10,
        strategies: [strategy({ instances: [instance()] })],
      }),
    ]);

    expect(rows().map((r) => r.dataset.fleetRow)).toEqual([
      "looping",
      "idle_rich",
    ]);
  });
});

describe("the money", () => {
  it("is a dash, never a zero, for a run that has claimed nothing", async () => {
    await render([agent({ strategies: [strategy()] })]);

    const net = pick("brigado").querySelector("[data-fleet-net]")!;
    const volume = pick("brigado").querySelector("[data-fleet-volume]")!;
    expect(net.textContent).toBe("—");
    expect(net.textContent).not.toContain("0.00");
    expect(volume.textContent).toContain("—");
  });

  it("is the attributed net and volume when the ledger has something to say", async () => {
    await render([
      agent({
        total_pnl: 64.12,
        total_volume: 2_549_843,
        strategies: [strategy()],
      }),
    ]);

    expect(
      pick("brigado").querySelector("[data-fleet-net]")!.textContent,
    ).toContain("+$64.12");
    expect(
      pick("brigado").querySelector("[data-fleet-volume]")!.textContent,
    ).toContain("$2.5M");
  });
});

describe("the last decision", () => {
  it("is a link into the tick that made it", async () => {
    await render([
      agent({
        status: "running",
        strategies: [
          strategy({
            instances: [
              instance({
                last_did: {
                  tick: 42,
                  at: 0,
                  tool: "manage_bots",
                  verb: "manage_bots:deploy",
                  summary: "Deploy brigado-brl_mm",
                  ok: true,
                  error: "",
                },
              }),
            ],
          }),
        ],
      }),
    ]);

    const link = pick("brigado").querySelector<HTMLAnchorElement>(
      "[data-fleet-decision] a",
    )!;
    expect(link.textContent).toContain("Deploy brigado-brl_mm");
    expect(link.getAttribute("href")).toBe(
      "/agents/brigado?view=tick&strategy=brl_mm&tick=42",
    );
  });

  it("falls back to what the loop last said when it has done nothing", async () => {
    await render([
      agent({
        status: "running",
        strategies: [
          strategy({
            instances: [instance({ last_action: "Held the range" })],
          }),
        ],
      }),
    ]);
    expect(
      pick("brigado").querySelector("[data-fleet-decision]")!.textContent,
    ).toContain("Held the range");
  });
});

describe("the next tick", () => {
  it("counts down on every looping row", async () => {
    vi.setSystemTime(new Date(1_030_000));
    await render([
      agent({
        status: "running",
        strategies: [strategy({ instances: [instance()] })],
      }),
    ]);
    expect(text()).toContain("next in");
  });
});

describe("the alerts", () => {
  it("surface a failed deed on the home page, addressed to its tick", async () => {
    await render([
      agent({
        status: "running",
        strategies: [
          strategy({
            instances: [
              instance({
                last_did: {
                  tick: 9,
                  at: 0,
                  tool: "manage_controllers",
                  verb: "manage_controllers:upsert",
                  summary: "Upsert controller pmm_1",
                  ok: false,
                  error: "rejected",
                },
              }),
            ],
          }),
        ],
      }),
    ]);

    const alert = pick("brigado").querySelector<HTMLAnchorElement>(
      '[data-fleet-alert="failed"]',
    )!;
    expect(alert.textContent).toContain("Upsert controller pmm_1");
    expect(alert.getAttribute("href")).toContain("tick=9");
  });
});

describe("an agent with no strategies", () => {
  it("renders once, as a name and a never-run, and not as a row", async () => {
    await render([
      agent({ slug: "bare", name: "Bare" }),
      agent({ slug: "brigado", strategies: [strategy()] }),
    ]);

    expect(rows().map((r) => r.dataset.fleetRow)).toEqual(["brigado"]);
    const listed = container.querySelectorAll('[data-fleet-strategyless="bare"]');
    expect(listed.length).toBe(1);
    expect(listed[0].textContent).toContain("never run");
  });
});
