/**
 * The Loops panel's own rows (FEAT-123): what each one says, and where a
 * click on it goes.
 *
 * The panel is a pure reader of the props `useLiveLoops` already filtered and
 * sorted — its own hook has that test — so what is pinned here is the empty
 * state, that every row says agent, strategy, session, tick and a countdown
 * whose wording matches an overdue tick against an upcoming one, and that a
 * click hands back exactly the row's own slugs.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LiveFleetOwner } from "@/hooks/useLiveLoops";
import type { LiveLoop } from "@/lib/agent-attribution";

import { LoopsPanel } from "./LoopsPanel";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const NOW_SEC = Math.floor(Date.now() / 1000);

function live(over: Partial<LiveLoop> = {}): LiveLoop {
  return {
    agentId: "a1",
    sessionNum: 4,
    status: "running",
    tickCount: 12,
    lastTickAt: NOW_SEC - 10,
    frequencySec: 30,
    lastAction: "",
    lastDid: null,
    lastError: "",
    ...over,
  };
}

function owner(over: Partial<LiveFleetOwner> = {}): LiveFleetOwner {
  return {
    runKey: "brigado.brl_mm",
    agentSlug: "brigado",
    agentName: "Brigado",
    strategySlug: "brl_mm",
    strategyName: "BRL MM",
    namespace: "brigado-brl_mm",
    declaredBots: [],
    agentIds: [],
    live: live(),
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

async function render(props: Partial<Parameters<typeof LoopsPanel>[0]> = {}) {
  await act(async () => {
    root.render(
      <LoopsPanel
        loops={[]}
        isLoading={false}
        onOpenLoop={() => {}}
        onClose={() => {}}
        {...props}
      />,
    );
  });
}

const panel = () =>
  container.querySelector<HTMLElement>('[data-testid="loops-panel"]')!;
const rows = () =>
  [...container.querySelectorAll<HTMLButtonElement>("[data-loop-row]")];

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

describe("LoopsPanel", () => {
  it("says nothing is looping with an empty list", async () => {
    await render({ loops: [] });
    expect(panel().textContent).toContain("Nothing looping right now");
  });

  it("says it is loading rather than claiming nothing is looping", async () => {
    await render({ loops: [], isLoading: true });
    expect(panel().textContent).not.toContain("Nothing looping right now");
    expect(panel().textContent).toContain("Loading");
  });

  it("renders one row per loop with agent, strategy, session and tick", async () => {
    await render({
      loops: [
        owner({ agentSlug: "brigado", agentName: "Brigado" }),
        owner({
          agentSlug: "vega",
          agentName: "Vega",
          strategySlug: "momentum",
          strategyName: "Momentum",
          live: live({ status: "paused", sessionNum: 2, tickCount: 5 }),
        }),
      ],
    });

    expect(rows()).toHaveLength(2);
    const [first, second] = rows();
    expect(first.textContent).toContain("Brigado");
    expect(first.textContent).toContain("BRL MM");
    expect(first.textContent).toContain("session 4");
    expect(first.textContent).toContain("tick 12");
    expect(second.textContent).toContain("Vega");
    expect(second.textContent).toContain("session 2");
    expect(second.textContent).toContain("tick 5");
  });

  it("shows an upcoming countdown for a tick not yet due", async () => {
    await render({
      loops: [
        owner({ live: live({ lastTickAt: NOW_SEC, frequencySec: 30 }) }),
      ],
    });
    expect(rows()[0].textContent).toMatch(/next in/);
  });

  it("shows an overdue countdown for a tick past its cadence", async () => {
    await render({
      loops: [
        owner({ live: live({ lastTickAt: NOW_SEC - 90, frequencySec: 30 }) }),
      ],
    });
    expect(rows()[0].textContent).toMatch(/overdue/);
  });

  it("calls onOpenLoop with the row's own slugs, exactly once", async () => {
    const onOpenLoop = vi.fn();
    await render({
      loops: [
        owner({ agentSlug: "brigado", strategySlug: "brl_mm" }),
        owner({ agentSlug: "vega", strategySlug: "momentum" }),
      ],
      onOpenLoop,
    });

    await act(async () => {
      rows()[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onOpenLoop).toHaveBeenCalledTimes(1);
    expect(onOpenLoop).toHaveBeenCalledWith("vega", "momentum");
  });
});
