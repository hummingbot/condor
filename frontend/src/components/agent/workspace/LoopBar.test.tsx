/**
 * What the loop is actually running, not just that it is running (FEAT-136).
 *
 * GitHub #275: an operator edited a config, saw "updated", and the running
 * agent kept the one it copied at start — and finding that out took reading
 * the logs. The bar is where it has to be visible, so these pin the two chips
 * that say it: the drift badge, and the model the adapter reports.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunningInstance, VenueCondition } from "@/lib/api";
import { LoopBar } from "./LoopBar";

let host: HTMLDivElement;
let root: Root;

const NOW = 1_800_000_000_000;

function instance(over: Partial<RunningInstance> = {}): RunningInstance {
  return {
    agent_id: "brigado.brl_mm_4",
    session_num: 4,
    status: "running",
    agent_key: "claude-acp:opus",
    tick_count: 14,
    realized_pnl: 0,
    unrealized_pnl: 0,
    total_pnl: 0,
    volume: 0,
    fees: 0,
    open_count: 0,
    closed_count: 0,
    win_rate: null,
    server_name: "brigado",
    total_amount_quote: 100,
    trading_context: "",
    frequency_sec: 60,
    tick_timeout_sec: 600,
    execution_mode: "loop",
    risk_limits: {},
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

function bar(live: RunningInstance | null) {
  return (
    <LoopBar
      strategies={[]}
      sslug="brl_mm"
      onSelectStrategy={() => {}}
      runs={[]}
      run={null}
      onSelectRun={() => {}}
      instance={live}
    />
  );
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

describe("config drift", () => {
  it("badges a session whose config no longer matches the file", async () => {
    await render(
      bar(
        instance({
          config_drift: {
            server_name: { running: "brigado", file: "moneymaker" },
          },
        }),
      ),
    );

    const badge = host.querySelector("[data-config-drift]");
    expect(badge).not.toBeNull();
    expect(badge!.textContent).toContain("config drifted");
    // Both values, on the badge itself: the point is not that something
    // changed but what a restart would change.
    expect(badge!.getAttribute("title")).toContain("running brigado");
    expect(badge!.getAttribute("title")).toContain("file moneymaker");
  });

  it("counts the drifted fields so one changed limit reads as one", async () => {
    await render(
      bar(
        instance({
          config_drift: {
            "risk_limits.max_leverage": { running: 5, file: 20 },
            server_name: { running: "brigado", file: "moneymaker" },
          },
        }),
      ),
    );

    expect(
      host.querySelector("[data-config-drift]")!.textContent,
    ).toContain("(2)");
  });

  it("badges nothing when the process and the file agree", async () => {
    await render(bar(instance({ config_drift: {} })));
    expect(host.querySelector("[data-config-drift]")).toBeNull();
  });

  it("badges nothing when the backend did not report drift at all", async () => {
    await render(bar(instance()));
    expect(host.querySelector("[data-config-drift]")).toBeNull();
  });
});

describe("the effective model", () => {
  it("shows what the adapter says it is on", async () => {
    await render(
      bar(
        instance({
          agent_key: "claude-acp:opus",
          active_model_id: "claude-opus-4-8",
        }),
      ),
    );

    const chip = host.querySelector("[data-effective-model]");
    expect(chip!.textContent).toContain("claude-opus-4-8");
    // The pin was honoured, so nothing is flagged.
    expect(chip!.textContent).not.toContain("pinned");
  });

  it("flags a pin the adapter did not apply", async () => {
    await render(
      bar(instance({ agent_key: "claude-acp:fable", active_model_id: "sonnet" })),
    );

    const chip = host.querySelector("[data-effective-model]");
    expect(chip!.textContent).toContain("sonnet");
    expect(chip!.textContent).toContain("pinned fable");
    expect(chip!.className).toContain("amber");
  });

  it("shows nothing when there is no adapter answer to report", async () => {
    // A pydantic-ai backend has no adapter to ask; echoing the requested key
    // back as the answer would be the audit answering itself.
    await render(bar(instance({ agent_key: "ollama:llama3.1" })));
    expect(host.querySelector("[data-effective-model]")).toBeNull();
  });
});

/**
 * A venue refusing us, worded as the venue's doing (FEAT-138).
 *
 * GitHub #276: "Throttling generally looked like a mysterious strategy bug or a
 * vendor outage, not like what it was." The bar is where the operator is
 * already watching the loop, so the condition has to be visible there — and it
 * has to name the venue and the counts, because sampling the logs said one host
 * was "intermittent" when it had answered 0 of 8 calls.
 */
describe("a throttled venue", () => {
  function throttled(over: Partial<VenueCondition> = {}): VenueCondition {
    return {
      host: "brigado",
      venue: "gate_io",
      condition: "throttled",
      attempts: 8,
      successes: 0,
      refusals: 8,
      errors: 0,
      last_refusal_at: NOW / 1000 - 5,
      last_reason: "venue returned HTTP 429 (too many requests)",
      ...over,
    };
  }

  it("names the venue and the refusals it made", async () => {
    await render(bar(instance({ throttled_venues: [throttled()] })));

    const badge = host.querySelector("[data-venue-throttled]");
    expect(badge).not.toBeNull();
    expect(badge!.textContent).toContain("gate_io");
    expect(badge!.textContent).toContain("8 refused");
  });

  it("puts the counts behind the claim on the badge itself", async () => {
    await render(bar(instance({ throttled_venues: [throttled()] })));

    const title = host
      .querySelector("[data-venue-throttled]")!
      .getAttribute("title")!;
    // The three numbers are the whole finding: 8 attempts, 0 ok, 8 refused is
    // "consistently refused", not "intermittent, may recover".
    expect(title).toContain("8 attempts, 0 ok, 8 refused");
    expect(title).toContain("not a strategy fault");
  });

  it("says it is the venue and never the agent", async () => {
    await render(
      bar(instance({ throttled_venues: [throttled()], last_error: "" })),
    );

    const badge = host.querySelector("[data-venue-throttled]")!;
    expect(badge.textContent).toContain("throttling");
    expect(badge.textContent).not.toMatch(/error|failed/i);
  });

  it("counts the venues when more than one is refusing", async () => {
    await render(
      bar(
        instance({
          throttled_venues: [
            throttled(),
            throttled({ venue: "kucoin", refusals: 3 }),
          ],
        }),
      ),
    );

    expect(
      host.querySelector("[data-venue-throttled]")!.textContent,
    ).toContain("2 venues throttling");
  });

  it("badges nothing for a venue that has stopped refusing", async () => {
    // The counts stay on the payload as the record; the badge is for what an
    // operator can act on now.
    await render(
      bar(instance({ throttled_venues: [throttled({ condition: "ok" })] })),
    );

    expect(host.querySelector("[data-venue-throttled]")).toBeNull();
  });

  it("badges nothing when the backend does not report venues at all", async () => {
    await render(bar(instance()));
    expect(host.querySelector("[data-venue-throttled]")).toBeNull();
  });
});
