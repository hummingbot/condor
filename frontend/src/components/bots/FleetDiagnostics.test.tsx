/**
 * Which of my bots is in trouble, and why (FEAT-141).
 *
 * The cases pinned here are the race's own, because every threshold in the view
 * was set from a measurement rather than invented:
 *
 * - the 4h45m case — `status: running`, no controller, nothing successful for
 *   an hour — and that the view names **all three** facts, since the diagnosis
 *   is the conjunction and no single one of them is wrong on its own;
 * - the failure bands, 0.3% against 93%, which is the difference between a
 *   volume strategy that was fine and the DEX path nobody saw for hours;
 * - the oversubscribed 2-vCPU host, which showed up as slow ticks rather than
 *   as a crash and so was read as a strategy bug;
 * - the executor history (35 / 173 / 0 / 2 / 16 at the end of the race) and the
 *   orphaned LP executor that polled for 13.5h with nobody owning it;
 * - and, running through all of them, that a signal this view could not read is
 *   drawn as **unknown** and never as ok. A diagnostics view that paints a
 *   missing reading green reproduces the exact defect it exists to surface
 *   (CORR-725).
 *
 * Needs a DOM for the three-state rendering, so this file overrides vitest's
 * default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DeedIndex, FleetOwner, LiveLoop } from "@/lib/agent-attribution";
import type {
  BotSummary,
  ExecutorHygiene,
  FleetDiagnostics as FleetDiagnosticsData,
} from "@/lib/api";
import { FleetDiagnostics } from "./FleetDiagnostics";
import {
  UNKNOWN_EXECUTOR_HYGIENE,
  UNKNOWN_HOST_LOAD,
  buildSignalGroups,
  flaggedCount,
  hostLoadSignal,
  hygieneSignals,
  livenessSignal,
  rateSignal,
  throttleSignals,
  type Signal,
  type SignalGroup,
} from "./fleet-diagnostics";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const NOW_S = 1_700_000_000;
const NOW = NOW_S * 1000;

function bot(over: Partial<BotSummary> = {}): BotSummary {
  return {
    bot_name: "brigado-brl_mm-20260914-101500",
    status: "running",
    num_controllers: 2,
    running_without_controllers: false,
    error_count: 0,
    deployed_at: null,
    error_logs: [],
    general_logs: [],
    ...over,
  };
}

function loop(over: Partial<LiveLoop> = {}): LiveLoop {
  return {
    agentId: "brigado.brl_mm_7",
    sessionNum: 7,
    status: "running",
    tickCount: 214,
    lastTickAt: NOW_S - 22,
    frequencySec: 60,
    lastReadingAt: NOW_S - 22,
    lastSuccessfulActionAt: NOW_S - 40,
    lastTickDuration: 1.4,
    lastAction: "Spreads held.",
    lastDid: null,
    lastError: "",
    ...over,
  };
}

function owner(live: LiveLoop | null = loop()): FleetOwner {
  return {
    runKey: "brigado.brl_mm",
    agentSlug: "brigado",
    agentName: "Brigado",
    strategySlug: "brl_mm",
    strategyName: "BRL MM",
    namespace: "brigado-brl_mm",
    declaredBots: [],
    agentIds: ["brigado.brl_mm_7"],
    live,
  };
}

function diagnostics(over: Partial<FleetDiagnosticsData> = {}): FleetDiagnosticsData {
  return { host: UNKNOWN_HOST_LOAD, api_lag: [], venues: [], callers: [], ...over };
}

const NO_DEEDS: DeedIndex = { bots: {}, since: 0 };

// ── The pure folds ──

describe("the 4h45m case", () => {
  it("flags a running bot with no controllers and nothing successful for an hour, naming all three facts", () => {
    const signal = livenessSignal(
      bot({ status: "running", num_controllers: 0, running_without_controllers: true }),
      loop({ lastSuccessfulActionAt: NOW_S - 3700, lastReadingAt: NOW_S - 30 }),
      NOW,
    );

    expect(signal.state).toBe("flagged");
    // All three, because the diagnosis is the conjunction: "running" was true,
    // and that is why nobody looked for 4h45m.
    expect(signal.facts).toContain("status running");
    expect(signal.facts).toContain("0 controllers");
    expect(signal.facts).toContain("acted 1h 01m ago");
  });

  it("leaves a bot that is trading and acting alone", () => {
    expect(livenessSignal(bot(), loop(), NOW).state).toBe("ok");
  });

  it("flags a bot that is trading but has not acted for an hour", () => {
    const signal = livenessSignal(bot(), loop({ lastSuccessfulActionAt: NOW_S - 7200 }), NOW);
    expect(signal.state).toBe("flagged");
    expect(signal.facts).toContain("2 controllers");
  });

  it("reads a bot no session owns as unknown, not as ok", () => {
    const signal = livenessSignal(bot(), null, NOW);
    expect(signal.state).toBe("unknown");
    expect(signal.facts.join(" ")).toContain("no agent session owns this bot");
  });

  it("still flags an unowned bot that is running with nothing trading", () => {
    // The container's own report is enough for this half: no session is needed
    // to see that nothing is inside it.
    expect(
      livenessSignal(bot({ num_controllers: 0, running_without_controllers: true }), null, NOW)
        .state,
    ).toBe("flagged");
  });

  it("reads a session that has not acted yet as unknown, not as idle forever", () => {
    // A fresh session adopting a healthy bot starts at 0. Read as an infinite
    // idle time it was flagged "nothing worked for 1h" in its first second.
    const signal = livenessSignal(bot(), loop({ lastSuccessfulActionAt: 0 }), NOW);
    expect(signal.state).toBe("unknown");
    expect(signal.facts).toContain("acted never");
    expect(signal.facts.join(" ")).toContain("has not acted yet");
  });

  it("still flags a bot with nothing trading under a session that has not acted", () => {
    // The two legs are independent: an unread idle time costs its own leg and
    // never the container's report.
    expect(
      livenessSignal(
        bot({ num_controllers: 0, running_without_controllers: true }),
        loop({ lastSuccessfulActionAt: 0 }),
        NOW,
      ).state,
    ).toBe("flagged");
  });
});

describe("the failure bands", () => {
  const counts = (attempts: number, failed: number) => ({
    attempts,
    successes: attempts - failed,
    refusals: failed,
    errors: 0,
  });

  it("does not flag a volume strategy failing 0.3% of its calls", () => {
    const signal = rateSignal("binance @ moneymaker", counts(1000, 3));
    expect(signal.state).toBe("ok");
    expect(signal.value).toBe("0.30%");
  });

  it("flags the DEX path failing 93%", () => {
    const signal = rateSignal("jupiter @ moneymaker", counts(1000, 930));
    expect(signal.state).toBe("flagged");
    expect(signal.value).toBe("93%");
  });

  it("states the band it judged against, so a borderline reading can be argued with", () => {
    expect(rateSignal("x", counts(1000, 3)).note).toContain("0.01–0.4%");
  });

  it("refuses to rate a sample too small to mean anything", () => {
    // One refusal out of one is 100% and says nothing. Unknown, not ok: "no
    // call has been tried" is not "nothing is wrong".
    expect(rateSignal("x", counts(1, 1)).state).toBe("unknown");
  });
});

describe("host capacity", () => {
  it("flags a 2-vCPU host above 1.0x load", () => {
    const signal = hostLoadSignal({
      available: true,
      load_1m: 2.4,
      vcpus: 2,
      load_per_vcpu: 1.2,
    });
    expect(signal.state).toBe("flagged");
    expect(signal.value).toBe("1.20x");
    expect(signal.facts).toContain("2 vCPU");
  });

  it("leaves the same load on sixteen cores alone", () => {
    expect(
      hostLoadSignal({ available: true, load_1m: 2.4, vcpus: 16, load_per_vcpu: 0.15 }).state,
    ).toBe("ok");
  });

  it("reads a host that reports no load average as unknown", () => {
    expect(hostLoadSignal(UNKNOWN_HOST_LOAD).state).toBe("unknown");
  });

  it("always draws all three host readings, each three-stated", () => {
    // Load, tick duration and placement lag. A group that omitted the one it
    // could not read would leave the reader to notice an absence.
    const [, , host] = buildSignalGroups({
      bots: [],
      owners: [],
      deeds: NO_DEEDS,
      diagnostics: diagnostics(),
      hygiene: UNKNOWN_EXECUTOR_HYGIENE,
      nowMs: NOW,
    });
    expect(host.signals.map((s) => s.label)).toEqual([
      "Load per vCPU",
      "Tick duration",
      "Placement lag",
    ]);
    expect(host.signals.every((s) => s.state === "unknown")).toBe(true);
  });

  it("reports a tick that outruns its own interval, and one that does not", () => {
    const read = (over: Partial<Parameters<typeof loop>[0]>) =>
      buildSignalGroups({
        bots: [],
        owners: [owner(loop(over))],
        deeds: NO_DEEDS,
        diagnostics: diagnostics({
          api_lag: [
            { host: "moneymaker", status: "online", last_latency_ms: 118, consecutive_failures: 0 },
          ],
        }),
        hygiene: UNKNOWN_EXECUTOR_HYGIENE,
        nowMs: NOW,
      })[2].signals;

    const slow = read({ frequencySec: 60, lastTickDuration: 92.5 });
    expect(slow[1]).toMatchObject({ label: "brigado.brl_mm tick", state: "flagged", value: "92.5s" });
    // And the lag reading beside it, which is what a slow tick is often *of*.
    expect(slow[2]).toMatchObject({ label: "moneymaker placement lag", value: "118 ms" });

    expect(read({ frequencySec: 60, lastTickDuration: 1.4 })[1].state).toBe("ok");
  });
});

describe("executor hygiene", () => {
  it("shows the terminal-row count and says when it is only a floor", () => {
    const [terminated] = hygieneSignals({
      ...UNKNOWN_EXECUTOR_HYGIENE,
      available: true,
      counted: 500,
      running: 3,
      terminated: 173,
      truncated: true,
    });
    expect(terminated.value).toBe("173 or more");
    expect(terminated.facts.join(" ")).toContain("the count is a floor");
  });

  it("flags a running executor whose owning session is gone, and names it", () => {
    const [, orphans] = hygieneSignals({
      ...UNKNOWN_EXECUTOR_HYGIENE,
      available: true,
      counted: 12,
      running: 2,
      terminated: 10,
      orphaned_running: [
        {
          executor_id: "ex-1",
          controller_id: "solana_dex_lp_expert.lp_slot_operator_3",
          agent_slug: "solana_dex_lp_expert",
          strategy_slug: "lp_slot_operator",
          session_num: 3,
          pair: "SOL-USDC",
          kind: "lp_executor",
        },
      ],
    });
    expect(orphans.state).toBe("flagged");
    expect(orphans.facts.join(" ")).toContain("ex-1");
    expect(orphans.facts.join(" ")).toContain("session 3");
  });

  it("reads a host with nothing cached as unknown on both counts", () => {
    expect(hygieneSignals(UNKNOWN_EXECUTOR_HYGIENE).map((s) => s.state)).toEqual([
      "unknown",
      "unknown",
    ]);
  });
});

describe("throttling", () => {
  it("shows refusals per venue and per agent", () => {
    const signals = throttleSignals(
      [
        {
          host: "moneymaker",
          venue: "jupiter",
          condition: "throttled",
          attempts: 8,
          successes: 0,
          refusals: 8,
          errors: 0,
          last_refusal_at: NOW_S - 30,
          last_reason: "429 Too Many Requests",
        },
      ],
      [
        {
          agent: "solana_dex_lp_expert",
          agent_id: "solana_dex_lp_expert.lp_slot_operator_3",
          host: "moneymaker",
          attempts: 4000,
          successes: 3000,
          refusals: 1000,
          errors: 0,
          last_refusal_at: NOW_S - 30,
          last_reason: "429 Too Many Requests",
        },
      ],
      NOW,
    );

    expect(signals.map((s) => s.label)).toEqual([
      "jupiter @ moneymaker",
      "solana_dex_lp_expert → moneymaker",
    ]);
    expect(signals.every((s) => s.state === "flagged")).toBe(true);
    // The venue row is a statement about the venue, which is the whole point:
    // reading a refusal as an agent failure is what made it look like a bug.
    expect(signals[0].note).toContain("never an agent error");
  });

  it("clears a caller whose refusals are all outside the window, like the venue row", () => {
    // `refusals` is a lifetime count, so flagging on it left the row red for
    // the rest of the process — hours of clean calls and a finished session
    // included — while the venue row beside it had long since cleared.
    const [venue, caller] = throttleSignals(
      [
        {
          host: "moneymaker",
          venue: "jupiter",
          condition: "ok",
          attempts: 8000,
          successes: 7000,
          refusals: 1000,
          errors: 0,
          last_refusal_at: NOW_S - 7200,
          last_reason: "429 Too Many Requests",
        },
      ],
      [
        {
          agent: "solana_dex_lp_expert",
          agent_id: "solana_dex_lp_expert.lp_slot_operator_3",
          host: "moneymaker",
          attempts: 8000,
          successes: 7000,
          refusals: 1000,
          errors: 0,
          last_refusal_at: NOW_S - 7200,
          last_reason: "429 Too Many Requests",
        },
      ],
      NOW,
    );

    expect(venue.state).toBe("ok");
    expect(caller.state).toBe("ok");
    // The lifetime count stays on the row as history; only the verdict moved.
    expect(caller.value).toBe("1000 refused");
    expect(caller.facts).toContain("last refusal 2h 00m ago");
  });

  it("reads no accounted call as unknown — never refused and never called look alike", () => {
    expect(throttleSignals([], [], NOW).map((s) => s.state)).toEqual(["unknown"]);
  });
});

// ── The rendering ──

let container: HTMLDivElement;
let root: Root;

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

async function render(groups: SignalGroup[]) {
  await act(async () => {
    root.render(<FleetDiagnostics groups={groups} />);
  });
}

const row = (label: string) =>
  container.querySelector<HTMLElement>(`[data-signal="${label}"]`);

describe("the three states on screen", () => {
  it("draws unknown visibly apart from ok", async () => {
    const signals: Signal[] = [
      { label: "an ok one", state: "ok", value: "live", facts: ["read 1s ago"] },
      { label: "an unknown one", state: "unknown", value: "unknown", facts: ["nothing cached"] },
      { label: "a flagged one", state: "flagged", value: "nothing trading", facts: ["0 controllers"] },
    ];
    await render([{ title: "Liveness", signals }]);

    const ok = row("an ok one")!;
    const unsure = row("an unknown one")!;
    const flagged = row("a flagged one")!;

    expect(ok.dataset.state).toBe("ok");
    expect(unsure.dataset.state).toBe("unknown");
    expect(flagged.dataset.state).toBe("flagged");
    // Not merely a different word: a different treatment, and specifically not
    // the ok one's.
    expect(unsure.className).not.toBe(ok.className);
    expect(unsure.className).toContain("border-dashed");
    expect(ok.className).not.toContain("border-dashed");
    expect(unsure.textContent).toContain("unknown");
  });

  it("draws all five groups, in triage order", async () => {
    const groups = buildSignalGroups({
      bots: [bot()],
      owners: [owner()],
      deeds: NO_DEEDS,
      diagnostics: diagnostics(),
      hygiene: UNKNOWN_EXECUTOR_HYGIENE,
      nowMs: NOW,
    });
    await render(groups);

    expect(
      [...container.querySelectorAll<HTMLElement>("[data-group]")].map(
        (g) => g.dataset.group,
      ),
    ).toEqual(["Liveness", "Activity", "Host", "Executors", "Throttling"]);
  });
});

describe("the fold the badge and the rows share", () => {
  it("counts every flagged signal once", () => {
    const groups = buildSignalGroups({
      bots: [bot({ num_controllers: 0, running_without_controllers: true })],
      owners: [owner(loop({ lastSuccessfulActionAt: NOW_S - 7200 }))],
      deeds: NO_DEEDS,
      diagnostics: diagnostics({
        host: { available: true, load_1m: 2.4, vcpus: 2, load_per_vcpu: 1.2 },
      }),
      hygiene: UNKNOWN_EXECUTOR_HYGIENE,
      nowMs: NOW,
    });

    // The bot and the host; the tick is inside its interval, and everything
    // else on this fixture is unknown rather than flagged.
    expect(flaggedCount(groups)).toBe(2);
  });

  it("flags nothing on a healthy fleet with every signal readable", () => {
    const hygiene: ExecutorHygiene = {
      available: true,
      counted: 12,
      running: 2,
      terminated: 10,
      truncated: false,
      orphaned_running: [],
    };
    const groups = buildSignalGroups({
      bots: [bot()],
      owners: [owner()],
      deeds: NO_DEEDS,
      diagnostics: diagnostics({
        host: { available: true, load_1m: 2.4, vcpus: 16, load_per_vcpu: 0.15 },
        api_lag: [
          { host: "moneymaker", status: "online", last_latency_ms: 120, consecutive_failures: 0 },
        ],
      }),
      hygiene,
      nowMs: NOW,
    });

    expect(flaggedCount(groups)).toBe(0);
  });
});
