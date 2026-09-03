/**
 * Now is the view the whole feature exists for, so it has to say three things.
 *
 * What needs a person, what the agent last decided **in full**, and what it put
 * into the world. The third is `DeploymentLedger`'s job and is pinned in its own
 * file; what is pinned here is the first two — that an alert is an address into
 * the tick that caused it, and that the decision arrives as rendered markdown
 * rather than as the asterisks and pipes a model actually writes.
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

import type { RunningInstance } from "@/lib/api";
import type { Decision } from "@/lib/parse-agent";
import { NowView } from "./NowView";
import { alertsFor } from "./views";

vi.mock("@/lib/api", () => ({
  api: {
    getSessionCanvas: vi.fn(async () => ({
      section_order: [],
      section_titles: {},
      sections: {},
    })),
  },
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

/** Only the three fields the countdown and the overdue rule actually read. */
function loop(): RunningInstance {
  return {
    status: "running",
    last_tick_at: 1_000,
    frequency_sec: 60,
  } as RunningInstance;
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
            instance={null}
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

describe("the next tick", () => {
  it("counts down while something is looping", async () => {
    vi.setSystemTime(new Date(1_030_000));
    await render({ instance: loop() });
    expect(text()).toContain("Next tick in");
    vi.useRealTimers();
  });

  it("says nothing is looping rather than counting down to nothing", async () => {
    await render({ instance: null });
    expect(text()).toContain("Nothing is looping");
  });
});
