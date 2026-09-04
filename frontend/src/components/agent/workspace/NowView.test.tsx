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

import type { AgentPerformance } from "@/lib/api";
import type { Decision, ParsedJournal } from "@/lib/parse-agent";
import { NowView } from "./NowView";
import { alertsFor } from "./views";

vi.mock("@/lib/api", () => ({
  api: {
    getSessionReport: vi.fn(async () => ({ report: null })),
  },
}));

// The report viewer reaches the theme through `window.matchMedia`, which jsdom
// does not have. It is the strip's own door and is pinned where it is built.
vi.mock("@/components/routines/ReportViewer", () => ({
  ReportViewer: () => <div data-report />,
}));

// The chart is `lightweight-charts` under a canvas jsdom does not have. What
// this file asserts about it is whether it was asked for, which the stub says.
vi.mock("@/components/agent/AgentSessionContent", async () => {
  const real = await vi.importActual<
    typeof import("@/components/agent/AgentSessionContent")
  >("@/components/agent/AgentSessionContent");
  return {
    ...real,
    SessionOverview: () => <div data-pnl-chart />,
  };
});

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

describe("the vitals", () => {
  it("lead the stack when the run has priced money in it", async () => {
    await render({ perf: traded(), journal: summary() });
    expect(text()).toContain("Total PnL");
    expect(text()).toContain("#14");
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
  it("is on the stack once the run has a journal to draw from", async () => {
    await render({ journal: summary() });
    expect(container.querySelector("[data-pnl-chart]")).not.toBeNull();
  });

  it("is absent for a run with no journal at all", async () => {
    await render({ journal: null });
    expect(container.querySelector("[data-pnl-chart]")).toBeNull();
  });
});

describe("the deployed table", () => {
  it("is on the stack, once", async () => {
    await render({});
    expect(text().split("Deployed").length - 1).toBe(1);
  });
});
