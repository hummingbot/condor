/**
 * The loop, said before anything else about the agent.
 *
 * Opening an agent in the chat's panel landed on Brain — its AGENT.md, the one
 * thing about an agent that never changes — and finding out whether it was
 * *running* cost three clicks. This strip is the fix, and it has exactly two
 * obligations: be there when something is looping, and be gone when nothing
 * is. A banner that is always present is chrome; one that appears only when it
 * has news is information.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunningInstance, StrategySummary } from "@/lib/api";
import { LoopBanner } from "./LoopBanner";

let host: HTMLDivElement;
let root: Root;

/** A fixed "now" so a countdown is a fact rather than a race. */
const NOW = 1_800_000_000_000;

function instance(over: Partial<RunningInstance> = {}): RunningInstance {
  return {
    agent_id: "brigado.brl_mm_4",
    session_num: 4,
    status: "running",
    agent_key: "sonnet",
    tick_count: 14,
    daily_pnl: 0,
    realized_pnl: 0,
    unrealized_pnl: 0,
    total_pnl: 0,
    volume: 0,
    fees: 0,
    open_count: 0,
    closed_count: 0,
    win_rate: null,
    server_name: "brigado_2",
    total_amount_quote: 100,
    trading_context: "",
    frequency_sec: 60,
    tick_timeout_sec: 600,
    execution_mode: "loop",
    risk_limits: {},
    // 23s to go on a 60s cadence.
    last_tick_at: NOW / 1000 - 37,
    max_ticks: 0,
    last_action: "",
    last_did: null,
    last_error: "",
    ...over,
  };
}

function strategy(over: Partial<StrategySummary> = {}): StrategySummary {
  return {
    slug: "brl_mm",
    name: "BRL MM",
    description: "",
    status: "idle",
    agent_id: "",
    session_count: 0,
    experiment_count: 0,
    tick_count: 0,
    daily_pnl: 0,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    instances: [],
    ...over,
  };
}

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

describe("when nothing is looping", () => {
  it("renders nothing at all", async () => {
    await render(
      <LoopBanner
        strategies={[strategy(), strategy({ slug: "ema", name: "EMA Trend" })]}
        onOpenStrategy={() => {}}
      />,
    );
    // Not an empty bar saying "no loops": a strip that is always there is
    // chrome, and chrome is what the reader learns to stop seeing.
    expect(host.querySelector('[data-testid="loop-banner"]')).toBeNull();
    expect(host.textContent).toBe("");
  });

  it("renders nothing for an agent that owns no strategies", async () => {
    await render(<LoopBanner strategies={[]} onOpenStrategy={() => {}} />);
    expect(host.textContent).toBe("");
  });
});

describe("when a loop is running", () => {
  it("names it and counts down to its next tick", async () => {
    await render(
      <LoopBanner
        strategies={[
          strategy(),
          strategy({
            slug: "fleet_op",
            name: "PMM King BTC-BRL Fleet Operator",
            status: "running",
            instances: [instance()],
          }),
        ]}
        onOpenStrategy={() => {}}
      />,
    );
    // The clock's first read is scheduled, not taken during render.
    await act(async () => {
      vi.advanceTimersByTime(1);
    });

    expect(host.textContent).toContain("PMM King BTC-BRL Fleet Operator");
    expect(host.textContent).toContain("tick 14");
    expect(host.textContent).toContain("next in 23s");
    // The idle sibling stays out of it — this strip is about what is live.
    expect(host.textContent).not.toContain("BRL MM");
  });

  it("calls a long tick overdue rather than printing a negative", async () => {
    await render(
      <LoopBanner
        strategies={[
          strategy({
            status: "running",
            instances: [instance({ last_tick_at: NOW / 1000 - 95 })],
          }),
        ]}
        onOpenStrategy={() => {}}
      />,
    );
    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    expect(host.textContent).toContain("overdue 35s");
    expect(host.textContent).not.toContain("-");
  });

  it("does not count down before the first tick", async () => {
    await render(
      <LoopBanner
        strategies={[
          strategy({ status: "running", instances: [instance({ last_tick_at: 0 })] }),
        ]}
        onOpenStrategy={() => {}}
      />,
    );
    // `0 + frequency` is 1970; a countdown from it is a number that means nothing.
    expect(host.textContent).toContain("first tick pending");
  });

  it("takes you to the loop in one click", async () => {
    const opened: string[] = [];
    await render(
      <LoopBanner
        strategies={[
          strategy({ slug: "fleet_op", status: "running", instances: [instance()] }),
        ]}
        onOpenStrategy={(sslug) => opened.push(sslug)}
      />,
    );
    await act(async () => {
      host.querySelector("button")?.click();
    });
    // "There is a loop running" and "take me to it" are one thought, so the
    // whole row is the door rather than growing a separate link.
    expect(opened).toEqual(["fleet_op"]);
  });

  it("lists every live loop, not just the first", async () => {
    await render(
      <LoopBanner
        strategies={[
          strategy({ slug: "a", name: "Alpha", status: "running", instances: [instance()] }),
          strategy({ slug: "b", name: "Beta", status: "running", instances: [instance()] }),
        ]}
        onOpenStrategy={() => {}}
      />,
    );
    expect(host.querySelectorAll("button").length).toBe(2);
    expect(host.textContent).toContain("Alpha");
    expect(host.textContent).toContain("Beta");
  });
});

describe("a paused loop", () => {
  it("is shown, and does not pretend to be counting down", async () => {
    await render(
      <LoopBanner
        strategies={[
          strategy({
            name: "BRL MM",
            status: "paused",
            instances: [instance({ status: "paused" })],
          }),
        ]}
        onOpenStrategy={() => {}}
      />,
    );
    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    // Paused is still a running-money state: it belongs on the strip. But a
    // paused loop has no next tick, so a countdown here would be fiction.
    expect(host.textContent).toContain("BRL MM");
    expect(host.textContent).toContain("paused");
    expect(host.textContent).not.toContain("next in");
  });
});

describe("a strategy whose engine outlives its status word", () => {
  it("is still shown, because the instance is the running thing", async () => {
    // `status` is the strategy's summary of its instances; the instance is the
    // fact. A live engine under an "idle" summary is exactly the disagreement
    // a reader opens the panel to catch.
    await render(
      <LoopBanner
        strategies={[strategy({ status: "idle", instances: [instance()] })]}
        onOpenStrategy={() => {}}
      />,
    );
    expect(host.textContent).toContain("BRL MM");
  });
});
