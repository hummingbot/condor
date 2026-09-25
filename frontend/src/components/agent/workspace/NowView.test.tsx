/**
 * The answer stack: everything the reader opens the page to find out, at once.
 *
 * Its vitals, what needs a person, what the agent last decided **in full**, what
 * it has earned and what it put into the world. The ledger and the chart are
 * their own components, pinned in their own files; what is pinned here is that
 * the five bands are on one screen — and, since FEAT-119, that the last action
 * is on it **once**: the strip printed it truncated to a line while the band
 * below printed it whole, which is the duplication the merge exists to remove.
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

import { api, type AgentPerformance, type ReportSummary } from "@/lib/api";
import type { Decision, ParsedJournal } from "@/lib/parse-agent";
import { NowView } from "./NowView";
import { alertsFor } from "./views";

vi.mock("@/lib/api", () => ({
  api: {
    getSessionReport: vi.fn(async () => ({ report: null })),
  },
}));

// The report's frame reaches the theme through `window.matchMedia`, which jsdom
// does not have. The viewer around it is real: its way out is pinned below.
vi.mock("@/components/routines/ReportFrame", () => ({
  ReportFrame: () => <div data-report />,
}));

// The chart is `lightweight-charts` under a canvas jsdom does not have. What
// this file asserts about it is whether it was asked for, which the stub says.
vi.mock("@/components/agent/session/SessionOverview", () => ({
  SessionOverview: ({ height }: { height: number }) => (
    <div data-pnl-chart data-height={height} />
  ),
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function decision(over: Partial<Decision> = {}): Decision {
  return {
    tick: 12,
    time: "2026-09-03 20:15",
    action: "Held the range",
    reasoning: "",
    riskNote: "",
    ...over,
  };
}

/** Enough priced money for the vitals strip to have something to print. */
function traded(): AgentPerformance {
  return {
    total_pnl: 64,
    realized_pnl: 40,
    unrealized_pnl: 24,
    volume: 12_000,
    fees: 3,
    trade_count: 8,
    open_count: 2,
  } as AgentPerformance;
}

/** A journal whose summary is what the strip reads — status and last tick. */
function summary(over: Partial<ParsedJournal["summary"]> = {}): ParsedJournal {
  return {
    summary: {
      status: "ACTIVE",
      lastTick: 14,
      lastAction: "Spreads held; BRL vol falling.",
      ...over,
    },
    metrics: [],
    decisions: [],
  } as unknown as ParsedJournal;
}

async function render(props: Partial<Parameters<typeof NowView>[0]> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <NowView
            slug="brigado"
            sslug="brl_mm"
            sessionNum={7}
            alerts={[]}
            decisions={[]}
            deployments={[]}
            perf={null}
            journal={null}
            onOpenTick={() => {}}
            {...props}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
}

const text = () => container.textContent ?? "";
const decisionBlock = () =>
  container.querySelector<HTMLElement>("[data-now-decision]")!;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the alerts", () => {
  it("lead the view, and each one is an address into its tick", async () => {
    const onOpenTick = vi.fn();
    await render({
      alerts: alertsFor({
        actions: [{ tick: 4, ok: false, summary: "Upsert controller pmm_1" }],
        deployments: 1,
        journalNamesDeploy: false,
        loop: null,
        nowSec: 0,
      }),
      onOpenTick,
    });

    const alert = container.querySelector<HTMLElement>('[data-alert="failed"]')!;
    expect(alert.textContent).toContain("Upsert controller pmm_1");

    await act(async () => {
      alert.querySelector("button")!.click();
    });
    expect(onOpenTick).toHaveBeenCalledWith(4);
  });

  it("are simply absent when the loop is healthy", async () => {
    await render({ alerts: [] });
    expect(container.querySelector("[data-now-alerts]")).toBeNull();
  });
});

describe("the last decision", () => {
  it("is the newest one, whole, and renders as markdown", async () => {
    await render({
      decisions: [
        decision({ tick: 11, action: "Older" }),
        decision({
          tick: 12,
          action: "Deployed **six** controllers",
          reasoning: "The `brl_mm` spread widened",
        }),
      ],
    });

    // Rendered, not printed: before this the reader got the asterisks and the
    // backticks, because the narrative went out as plain text.
    expect(decisionBlock().querySelector("strong")?.textContent).toBe("six");
    expect(decisionBlock().querySelector("code")?.textContent).toBe("brl_mm");
    expect(decisionBlock().textContent).toContain("Deployed");
    expect(decisionBlock().textContent).not.toContain("Older");
  });

  it("opens the tick it came from", async () => {
    const onOpenTick = vi.fn();
    await render({ decisions: [decision({ tick: 12 })], onOpenTick });

    await act(async () => {
      decisionBlock().querySelector("button")!.click();
    });
    expect(onOpenTick).toHaveBeenCalledWith(12);
  });

  it("says the run has decided nothing rather than showing an empty card", async () => {
    await render({ decisions: [] });
    expect(decisionBlock().textContent).toContain("has not decided anything yet");
  });
});

/** A bot-history PnL series with `n` points, a minute apart. */
function series(n: number): { timestamp: string; pnl: number }[] {
  return Array.from({ length: n }, (_, i) => ({
    timestamp: new Date(Date.UTC(2026, 8, 3, 20, i)).toISOString(),
    pnl: i * 2,
  }));
}

const card = () => container.querySelector<HTMLElement>("[data-now-money]");
const reportButton = () =>
  [...container.querySelectorAll<HTMLButtonElement>("button")].find((b) =>
    b.textContent?.includes("Session report"),
  );

describe("the vitals", () => {
  it("lead the stack when the run has priced money in it", async () => {
    await render({ perf: traded(), journal: summary() });
    expect(text()).toContain("Total PnL");
  });

  it("leave the status and the tick count to the loop bar above (ARCH-426)", async () => {
    await render({ perf: traded(), journal: summary({ status: "ACTIVE", lastTick: 14 }) });
    const kpis = container.querySelector<HTMLElement>("[data-session-kpis]")!;
    expect(kpis.textContent).not.toContain("Status");
    expect(kpis.textContent).not.toContain("ACTIVE");
    expect(kpis.textContent).not.toContain("Ticks");
    expect(text()).not.toContain("#14");
  });

  it("clamp a long close breakdown to one line, whole in the tooltip", async () => {
    const breakdown = { "CloseType.POSITION_HOLD": 454, "CloseType.EARLY_STOP": 44806 };
    await render({
      perf: { ...traded(), trade_count: 0, close_type_counts: breakdown } as AgentPerformance,
      journal: summary(),
    });
    const sub = [...container.querySelectorAll<HTMLElement>("[data-session-kpis] [title]")].find(
      (el) => el.textContent?.includes("Closes"),
    )!;
    expect(sub).toBeDefined();
    expect(sub.title).toContain("44806");
    expect(sub.querySelector(".truncate")).not.toBeNull();
  });

  it("give way to the reason when the server could not be read (CORR-430)", async () => {
    await render({ perf: null, journal: summary(), unavailable: "no_access" });
    expect(container.querySelector("[data-now-unavailable]")?.textContent).toBe(
      "Server access unavailable for this strategy",
    );
    expect(text()).not.toContain("Total PnL");
    expect(text()).not.toContain("$0");
  });

  it('say nothing extra when "unavailable" is ""', async () => {
    await render({ perf: null, journal: summary(), unavailable: "" });
    expect(container.querySelector("[data-now-unavailable]")).toBeNull();
  });

  it("are absent for a run that never traded, rather than eight zeroes", async () => {
    await render({ perf: null, journal: summary() });
    expect(text()).not.toContain("Total PnL");
  });

  it("print the last action nowhere — the band below has it whole", async () => {
    // The duplication FEAT-119 removes: the strip truncated the sentence to one
    // line six pixels above the band that renders it as markdown.
    await render({
      perf: traded(),
      journal: summary(),
      decisions: [decision({ action: "Spreads held; BRL vol falling." })],
    });
    const whole = text().split("Spreads held").length - 1;
    expect(whole).toBe(1);
    expect(text()).not.toContain("Last action");
  });
});

describe("the realized-PnL chart", () => {
  it("is on the stack once the run has two points to draw", async () => {
    await render({ journal: summary(), pnlSeries: series(2) });
    expect(container.querySelector("[data-pnl-chart]")).not.toBeNull();
  });

  it("is absent under two points — a dot is not a curve", async () => {
    await render({ journal: summary(), pnlSeries: series(1) });
    expect(container.querySelector("[data-pnl-chart]")).toBeNull();
    await render({ journal: summary(), pnlSeries: [] });
    expect(container.querySelector("[data-pnl-chart]")).toBeNull();
  });

  it("is absent for a run with no journal at all", async () => {
    await render({ journal: null, pnlSeries: series(3) });
    expect(container.querySelector("[data-pnl-chart]")).toBeNull();
  });

  it("is shorter in the side panel than on the page", async () => {
    await render({ journal: summary(), pnlSeries: series(3), variant: "pane" });
    const pane = Number(container.querySelector<HTMLElement>("[data-pnl-chart]")!.dataset.height);
    await render({ journal: summary(), pnlSeries: series(3), variant: "page" });
    const page = Number(container.querySelector<HTMLElement>("[data-pnl-chart]")!.dataset.height);
    expect(page).toBe(400);
    expect(pane).toBeGreaterThanOrEqual(240);
    expect(pane).toBeLessThanOrEqual(280);
  });
});

describe("the money card (ARCH-426)", () => {
  const report: ReportSummary = {
    id: "r1",
    title: "Session 7 report",
    filename: "r1.html",
    created_at: "2026-09-03T20:15:00Z",
    source_type: "agent",
    source_name: "brl_mm",
    tags: [],
  };

  beforeEach(() => {
    vi.mocked(api.getSessionReport).mockResolvedValue({ report } as never);
  });

  afterEach(() => {
    vi.mocked(api.getSessionReport).mockResolvedValue({ report: null } as never);
  });

  /** Render and let the report query land, bounded. */
  async function renderWithReport(props: Partial<Parameters<typeof NowView>[0]>) {
    await render(props);
    for (let i = 0; i < 50 && !reportButton(); i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }
  }

  it("holds the vitals, the report door in its header and the chart, in one border", async () => {
    await renderWithReport({ perf: traded(), journal: summary(), pnlSeries: series(3) });
    const money = card()!;
    expect(money).not.toBeNull();
    expect(container.querySelectorAll("[data-now-money]")).toHaveLength(1);
    expect(money.querySelector("header")!.textContent).toContain("Realized PnL");
    expect(money.querySelector("header")!.contains(reportButton()!)).toBe(true);
    expect(money.querySelector("[data-session-kpis]")).not.toBeNull();
    expect(money.querySelector("[data-pnl-chart]")).not.toBeNull();
    // The door sits in the header, never in the KPI row it used to wrap out of.
    expect(money.querySelector("[data-session-kpis]")!.contains(reportButton()!)).toBe(false);
  });

  it("comes before the alerts and the last decision", async () => {
    await render({
      perf: traded(),
      journal: summary(),
      pnlSeries: series(3),
      alerts: alertsFor({
        actions: [{ tick: 4, ok: false, summary: "Upsert controller pmm_1" }],
        deployments: 1,
        journalNamesDeploy: false,
        loop: null,
        nowSec: 0,
      }),
    });
    const money = card()!;
    const alerts = container.querySelector("[data-now-alerts]")!;
    const follows = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(money.compareDocumentPosition(alerts) & follows).toBeTruthy();
    expect(money.compareDocumentPosition(decisionBlock()) & follows).toBeTruthy();
  });

  it("shows the vitals and the door alone when there is no chart yet", async () => {
    await renderWithReport({ perf: traded(), journal: summary(), pnlSeries: series(1) });
    const money = card()!;
    expect(money.querySelector("[data-session-kpis]")).not.toBeNull();
    expect(money.contains(reportButton()!)).toBe(true);
    expect(money.querySelector("[data-pnl-chart]")).toBeNull();
  });

  it("shows the chart without vitals for a run with a curve but no priced money", async () => {
    await render({ perf: null, journal: summary(), pnlSeries: series(3) });
    const money = card()!;
    expect(money.querySelector("[data-pnl-chart]")).not.toBeNull();
    expect(money.querySelector("[data-session-kpis]")).toBeNull();
  });

  it("keeps a lone report door when there is neither money nor a curve", async () => {
    await renderWithReport({ perf: null, journal: summary() });
    expect(card()).toBeNull();
    expect(reportButton()).toBeDefined();
  });

  it("is absent altogether when there is nothing to put in it", async () => {
    await render({ perf: null, journal: summary() });
    expect(card()).toBeNull();
    expect(reportButton()).toBeUndefined();
  });
});

describe("the deployed table", () => {
  it("is on the stack, once", async () => {
    await render({});
    expect(text().split("Deployed").length - 1).toBe(1);
  });
});

describe("a strategy whose runs are outside the loaded window (CORR-376)", () => {
  it("says so and offers to widen it, rather than that it never ran", async () => {
    const onShowOlderRuns = vi.fn();
    await render({ sessionNum: 0, onShowOlderRuns });
    expect(text()).not.toContain("This strategy has not run yet.");
    const more = container.querySelector<HTMLButtonElement>("[data-show-older-runs]")!;
    expect(more).not.toBeNull();
    await act(async () => more.click());
    expect(onShowOlderRuns).toHaveBeenCalledTimes(1);
  });

  it("keeps `has not run yet` for a strategy that genuinely has not", async () => {
    await render({ sessionNum: 0 });
    expect(text()).toContain("This strategy has not run yet.");
    expect(container.querySelector("[data-show-older-runs]")).toBeNull();
  });
});

describe("the session report (CORR-422)", () => {
  const report: ReportSummary = {
    id: "r1",
    title: "Session 7 report",
    filename: "r1.html",
    created_at: "2026-09-03T20:15:00Z",
    source_type: "agent",
    source_name: "brl_mm",
    tags: [],
  };

  beforeEach(() => {
    vi.mocked(api.getSessionReport).mockResolvedValue({ report } as never);
  });

  afterEach(() => {
    vi.mocked(api.getSessionReport).mockResolvedValue({ report: null } as never);
  });

  /** Render, wait for the report query to land, and open the report. */
  async function openReport(props: Partial<Parameters<typeof NowView>[0]> = {}) {
    await render(props);
    let door: HTMLButtonElement | undefined;
    for (let i = 0; i < 50 && !door; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      door = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
        (b) => b.textContent?.includes("Session report"),
      );
    }
    expect(door).toBeDefined();
    await act(async () => door!.click());
    expect(container.querySelector("[data-now-report]")).not.toBeNull();
  }

  it("has a way back to Now, and taking it closes the report", async () => {
    await openReport();
    const back = container.querySelector<HTMLButtonElement>("[data-report-close]");
    expect(back).not.toBeNull();
    await act(async () => back!.click());
    expect(container.querySelector("[data-now-report]")).toBeNull();
    expect(container.querySelector("[data-report]")).toBeNull();
  });

  it("closes on Escape", async () => {
    await openReport();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(container.querySelector("[data-now-report]")).toBeNull();
  });

  it("covers the pane only in the side panel, and the window on the page", async () => {
    await openReport({ variant: "pane" });
    const pane = container.querySelector<HTMLElement>("[data-now-report]")!;
    expect(pane.className).toContain("absolute");
    expect(pane.className).not.toContain("fixed");

    await openReport({ variant: "page" });
    const page = container.querySelector<HTMLElement>("[data-now-report]")!;
    expect(page.className).toContain("fixed");
  });
});
