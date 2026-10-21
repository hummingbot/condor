/**
 * The diagnostics drawer, in the view the operator already arrived at (FEAT-141).
 *
 * `FleetDiagnostics.test.tsx` pins the verdicts; this pins the three things
 * only the host can be wrong about:
 *
 * - the drawer is reachable **at fleet scope**, because the operator who needs
 *   it has not yet worked out which bot to click on — a separate place to go is
 *   the reason the race's symptoms went unnoticed for hours;
 * - the log viewer issue #64 asked for still renders, unchanged, in the same
 *   slot. `LogsSection` is deliberately *not* mocked here;
 * - opening it costs **no** request. The whole constraint on this feature is
 *   that it adds no polling to a dashboard that already polls per server, so
 *   every signal rides on a payload the page already had.
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

import type { BotSummary, ControllerInfo, ExecutorHygiene } from "@/lib/api";
import type { FleetOwner } from "@/lib/agent-attribution";

/** Every call the browser makes, so "no new poll" is a measurement. */
const spy = vi.hoisted(() => ({ calls: [] as string[] }));

vi.mock("@/lib/api", () => ({
  api: new Proxy(
    {},
    {
      get: (_target, name) => {
        return () => {
          spy.calls.push(String(name));
          return Promise.resolve({});
        };
      },
    },
  ),
}));

vi.mock("@/components/bots/PnlEvolutionChart", () => ({ PnlEvolutionChart: () => null }));
vi.mock("@/components/bots/ControllerPnlChart", () => ({ ControllerPnlChart: () => null }));
vi.mock("@/components/charts/ExecutorChart", () => ({ ExecutorChart: () => null }));
vi.mock("@/components/editor/EditorModal", () => ({ EditorModal: () => null }));
vi.mock("@/components/bots/DeployBotDialog", () => ({ DeployBotDialog: () => null }));
vi.mock("@/components/bots/ArchivedBotDetail", () => ({ ArchivedBotDetail: () => null }));
vi.mock("@/components/perf/YamlConfigEditor", () => ({ YamlConfigEditor: () => null }));
vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => {} }));

const { PerfBrowser } = await import("@/components/perf/PerfBrowser");
const { UNKNOWN_DIAGNOSTICS, UNKNOWN_EXECUTOR_HYGIENE } = await import(
  "@/components/bots/fleet-diagnostics"
);

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const NOW_S = Math.floor(Date.now() / 1000);

const CONTROLLERS = [
  {
    controller_name: "pmm_simple",
    controller_type: "",
    controller_id: "c1",
    bot_name: "alpha-mm-1",
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 30,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 30,
    global_pnl_pct: 0,
    volume_traded: 900,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    config: {},
  } as unknown as ControllerInfo,
];

/** The 4h45m case: the container is up, nothing is inside it, nothing worked. */
const BOTS: BotSummary[] = [
  {
    bot_name: "alpha-mm-1",
    status: "running",
    num_controllers: 0,
    running_without_controllers: true,
    error_count: 1,
    deployed_at: null,
    error_logs: [{ timestamp: NOW_S, msg: "order placement timed out" }],
    general_logs: [],
  },
];

const OWNERS: FleetOwner[] = [
  {
    runKey: "alpha.mm",
    agentSlug: "alpha",
    agentName: "Alpha",
    strategySlug: "mm",
    strategyName: "MM",
    namespace: "alpha-mm",
    declaredBots: [],
    agentIds: [],
    live: {
      agentId: "alpha.mm_2",
      sessionNum: 2,
      status: "running",
      tickCount: 90,
      lastTickAt: NOW_S - 10,
      frequencySec: 60,
      lastReadingAt: NOW_S - 10,
      lastSuccessfulActionAt: NOW_S - 5400,
      lastTickDuration: 2.1,
      lastAction: "Holding.",
      lastDid: null,
      lastError: "",
    },
  },
];

const HYGIENE: ExecutorHygiene = {
  available: true,
  counted: 20,
  running: 1,
  terminated: 19,
  truncated: false,
  orphaned_running: [
    {
      executor_id: "ex-7",
      controller_id: "alpha.mm_1",
      agent_slug: "alpha",
      strategy_slug: "mm",
      session_num: 1,
      pair: "SOL-USDC",
      kind: "lp_executor",
    },
  ],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  spy.calls = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render({
  hygiene = HYGIENE,
  url = "/bots",
}: { hygiene?: ExecutorHygiene; url?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[url]}>
          <PerfBrowser
            controllers={CONTROLLERS}
            bots={BOTS}
            server="s1"
            convert={(value: number) => ({ value, converted: true })}
            currencySymbol="$"
            snapshots={[]}
            owners={OWNERS}
            deeds={{ bots: {}, since: 1 }}
            diagnostics={UNKNOWN_DIAGNOSTICS}
            hygiene={hygiene}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const button = (label: string) =>
  [...container.querySelectorAll<HTMLButtonElement>("button")].find((b) =>
    b.textContent?.trim().startsWith(label),
  );

async function click(label: string) {
  const el = button(label);
  if (!el) throw new Error(`no "${label}" button on screen`);
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

const groups = () =>
  [...container.querySelectorAll<HTMLElement>("[data-group]")].map((g) => g.dataset.group);

const signal = (label: string) =>
  container.querySelector<HTMLElement>(`[data-signal="${label}"]`);

describe("the drawer", () => {
  it("opens at fleet scope and draws the five groups", async () => {
    await render();
    expect(groups()).toEqual([]);

    await click("Diagnostics");

    expect(groups()).toEqual(["Liveness", "Activity", "Host", "Executors", "Throttling"]);
  });

  it("names all three facts of the 4h45m case on screen", async () => {
    await render();
    await click("Diagnostics");

    const row = signal("alpha-mm-1")!;
    expect(row.dataset.state).toBe("flagged");
    expect(row.textContent).toContain("status running");
    expect(row.textContent).toContain("0 controllers");
    // The page's clock is quantised to the minute (`useCoarseClock`), so the
    // minutes read 29 or 30 depending on where in the minute the test lands;
    // the fact being asserted is that the age is named and is over the hour.
    expect(row.textContent).toMatch(/acted 1h \d\dm ago/);
  });

  it("names the orphaned executor its own session left running", async () => {
    await render();
    await click("Diagnostics");

    const row = signal("Orphaned running")!;
    expect(row.dataset.state).toBe("flagged");
    expect(row.textContent).toContain("ex-7");
  });

  it("draws a host with nothing cached as unknown rather than as clean", async () => {
    await render({ hygiene: UNKNOWN_EXECUTOR_HYGIENE });
    await click("Diagnostics");

    expect(signal("Orphaned running")!.dataset.state).toBe("unknown");
    expect(signal("Terminated executors")!.dataset.state).toBe("unknown");
  });

  it("counts the flagged signals on the button, before it is opened", async () => {
    await render();
    // The bot, the orphan, and the host's own load is unknown rather than
    // flagged on this fixture — so the badge is what is actually wrong.
    const badge = container.querySelector<HTMLElement>("[data-diagnostics-flagged]");
    expect(badge?.dataset.diagnosticsFlagged).toBe("2");
  });

  it("issues no request when it is opened", async () => {
    await render();
    const before = [...spy.calls];

    await click("Diagnostics");

    expect(spy.calls).toEqual(before);
  });
});

describe("the log viewer #64 asked for", () => {
  it("still renders the bot's logs, in the same slot", async () => {
    // Bot scope, since the logs belong to a bot rather than to the fleet.
    await render({ url: "/bots?scope=bot:alpha-mm-1" });

    await click("Logs");

    expect(container.textContent).toContain("order placement timed out");
    // The filter row LogsSection has always drawn.
    expect(button("all (1)")).toBeTruthy();
  });

  it("gives the drawer to whichever of the two was asked for last", async () => {
    await render({ url: "/bots?scope=bot:alpha-mm-1" });

    await click("Logs");
    expect(container.textContent).toContain("order placement timed out");

    await click("Diagnostics");
    expect(groups()).toHaveLength(5);
    expect(container.textContent).not.toContain("order placement timed out");
  });
});
