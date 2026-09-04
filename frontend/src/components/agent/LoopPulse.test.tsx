/**
 * What the loop pulse promises.
 *
 * A strategy *is* a loop, and every surface that showed one used to describe it
 * as a status word beside a row of PnL. Five things this has to say instead,
 * and none of them are decorative:
 *
 * 1. An **idle** loop still names its cadence — otherwise Start is a leap.
 * 2. A **running** loop counts down to the next tick, second by second.
 * 3. A tick running **long** is called overdue rather than printed as a
 *    negative countdown, which reads as a bug in the page.
 * 4. A tick is an **address**: clicking a beat opens that tick's snapshot.
 * 5. What the loop **did** and what it **said** are two statements, and a
 *    failure is neither hidden nor dressed up as a success.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunningInstance } from "@/lib/api";
import { LoopPulse } from "./LoopPulse";

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

describe("an idle loop", () => {
  it("names the cadence it would run on", async () => {
    await render(
      <LoopPulse instance={null} status="idle" config={{ frequency_sec: 300 }} />,
    );
    // The whole point of saying it before anything runs: Start is legible.
    expect(host.textContent).toContain("every 5m 00s");
    expect(host.textContent).toContain("no ticks yet");
  });

  it("says idle, not running", async () => {
    await render(<LoopPulse instance={null} status="idle" config={{}} />);
    expect(host.textContent).toContain("idle");
  });
});

describe("a running loop", () => {
  it("counts down to the next tick", async () => {
    await render(
      <LoopPulse instance={instance()} status="running" config={{}} />,
    );
    // The clock's first read is scheduled, not taken during render.
    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    expect(host.textContent).toContain("next in 23s");
  });

  it("calls a long tick overdue rather than printing a negative", async () => {
    await render(
      <LoopPulse
        instance={instance({ last_tick_at: NOW / 1000 - 95 })}
        status="running"
        config={{}}
      />,
    );
    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    expect(host.textContent).toContain("overdue 35s");
    expect(host.textContent).not.toContain("-");
  });

  it("has not counted down before its first tick", async () => {
    await render(
      <LoopPulse
        instance={instance({ last_tick_at: 0, tick_count: 0 })}
        status="running"
        config={{}}
      />,
    );
    // 0 + frequency is 1970; a bar filled from there means nothing.
    expect(host.textContent).toContain("first tick pending");
  });

  it("draws one beat per tick, capped at a strip", async () => {
    await render(
      <LoopPulse
        instance={instance({ tick_count: 40 })}
        status="running"
        config={{}}
      />,
    );
    const beats = host.querySelectorAll('[aria-label^="Tick "]');
    // A pulse, not a history — the reviewer owns the history.
    expect(beats.length).toBe(12);
    expect(beats[beats.length - 1].getAttribute("aria-label")).toBe("Tick 40");
  });
});

describe("a tick is an address", () => {
  it("opens that tick's snapshot when a beat is clicked", async () => {
    const opened: Array<[number, number]> = [];
    await render(
      <LoopPulse
        instance={instance({ tick_count: 3 })}
        status="running"
        config={{}}
        onOpenTick={(s, t) => opened.push([s, t])}
      />,
    );
    const beat = host.querySelector('[aria-label="Tick 2"]') as HTMLButtonElement;
    await act(async () => {
      beat.click();
    });
    expect(opened).toEqual([[4, 2]]);
  });

  it("leaves the beats inert when the host has nowhere to send them", async () => {
    await render(
      <LoopPulse instance={instance({ tick_count: 3 })} status="running" config={{}} />,
    );
    const beat = host.querySelector('[aria-label="Tick 2"]') as HTMLButtonElement;
    expect(beat.disabled).toBe(true);
  });
});

describe("what it did and what it said", () => {
  const did = {
    tick: 14,
    at: NOW / 1000,
    tool: "manage_bots",
    verb: "manage_bots:deploy",
    summary: "Deploy pmm_btc_brl",
    ok: true,
    error: "",
  };

  it("shows the deed and the narration as separate statements", async () => {
    await render(
      <LoopPulse
        instance={instance({ last_did: did, last_action: "spreads widened" })}
        status="running"
        config={{}}
      />,
    );
    expect(host.textContent).toContain("Deploy pmm_btc_brl");
    // The narration is quoted, because it is the model's words and not a fact.
    expect(host.textContent).toContain("“spreads widened”");
  });

  it("says a failed deed failed", async () => {
    await render(
      <LoopPulse
        instance={instance({ last_did: { ...did, ok: false, error: "no such bot" } })}
        status="running"
        config={{}}
      />,
    );
    expect(host.textContent).toContain("failed: no such bot");
  });

  it("surfaces the engine's own error instead of swallowing it", async () => {
    await render(
      <LoopPulse
        instance={instance({ last_error: "No API client available" })}
        status="running"
        config={{}}
      />,
    );
    expect(host.textContent).toContain("No API client available");
  });
});
