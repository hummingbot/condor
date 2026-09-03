/**
 * The band says what the agent did *and* what it said (FEAT-097).
 *
 * Before the action log this header had one line to offer and it was the
 * agent's own narration — `response_text[:100]`, dressed up as "Last action".
 * The deed line is the fact beside it, and these cases pin the three ways that
 * could go wrong: the two statements collapsing into one, a failed call reading
 * as a success, and — the regression that matters most — a session that ran
 * before the log existed suddenly rendering differently. Nothing is backfilled,
 * so that band must be exactly what it was.
 *
 * The click is the fourth: the deed carries the tick it happened on, and
 * landing on that tick is what collapses the main page → agent → session →
 * snapshot walk.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentActionRow, FleetOwner, LiveLoop } from "@/lib/agent-attribution";
import { AgentScopeHeader } from "./AgentScopeHeader";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return { ...actual, useNavigate: () => navigate };
});

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function did(over: Partial<AgentActionRow> = {}): AgentActionRow {
  return {
    tick: 212,
    at: 1_700_000_000,
    tool: "create_grid_executor",
    verb: "create_grid_executor",
    summary: "Create grid executor on SOL-USDC for 100 quote",
    ok: true,
    error: "",
    ...over,
  };
}

function owner(live: Partial<LiveLoop> | null): FleetOwner {
  return {
    runKey: "brigado.brl_mm",
    agentSlug: "brigado",
    agentName: "Brigado",
    strategySlug: "brl_mm",
    strategyName: "BRL MM",
    namespace: "brigado-brl_mm",
    declaredBots: [],
    agentIds: ["brigado.brl_mm_7"],
    live: live && {
      agentId: "brigado.brl_mm_7",
      sessionNum: 7,
      status: "running",
      tickCount: 214,
      lastTickAt: Math.floor(Date.now() / 1000),
      frequencySec: 60,
      lastAction: "Spreads held; BTC vol falling, widening the ask side.",
      lastDid: null,
      lastError: "",
      ...live,
    },
  };
}

let container: HTMLDivElement;
let root: Root;

async function render(o: FleetOwner) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <AgentScopeHeader runKey={o.runKey} owner={o} />
      </MemoryRouter>,
    );
  });
}

const deed = () =>
  [...document.querySelectorAll<HTMLElement>("button")].find((b) =>
    b.textContent?.includes("#"),
  );

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  navigate.mockClear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the deed line", () => {
  it("names the last thing the agent actually did, beside what it said", async () => {
    await render(owner({ lastDid: did() }));

    expect(container.textContent).toContain("#212");
    expect(container.textContent).toContain("Create grid executor on SOL-USDC");
    // Two statements, not one: the narration is still there and still quoted.
    expect(container.textContent).toContain("Spreads held;");
  });

  it("shows only the words for a session that has not acted", async () => {
    await render(owner({ lastDid: null }));

    expect(container.textContent).not.toContain("#212");
    expect(container.textContent).toContain("Spreads held;");
  });

  it("marks a failed deed rather than reporting it as done", async () => {
    await render(
      owner({
        lastDid: did({
          ok: false,
          error: "ToolError: insufficient balance",
          summary: "Create grid executor on SOL-USDC for 100 quote",
        }),
      }),
    );

    expect(container.textContent).toContain("failed");
    expect(container.textContent).toContain("insufficient balance");
  });

  // The address is a URL, not `location.state` (FEAT-099): a tick nobody can
  // copy out of the address bar is a tick nobody can send to anyone.
  it("opens the tick the deed happened on, at a linkable URL", async () => {
    await render(owner({ lastDid: did({ tick: 212 }) }));

    await act(async () => {
      deed()!.click();
    });

    expect(navigate).toHaveBeenCalledWith(
      "/agents/brigado/runs?strategy=brl_mm&run=s7&tick=212",
    );
  });

  it("still opens the run plainly from the Open session button", async () => {
    await render(owner({ lastDid: did() }));

    await act(async () => {
      [...document.querySelectorAll<HTMLElement>("button")]
        .find((b) => b.textContent?.includes("Open session"))!
        .click();
    });

    expect(navigate).toHaveBeenCalledWith("/agents/brigado/runs?strategy=brl_mm&run=s7");
  });
});
