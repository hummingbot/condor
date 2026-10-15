/**
 * The dashboard half of scope labelling (FEAT-135).
 *
 * The KPI strip's Realized and Volume are one number folded from this session's
 * ownership window and the bot name's whole lifetime, and nothing in them said
 * which — that is how a session summary came to report $200 for a session that
 * earned $120, $80 of it belonging to a stopped earlier generation of the same
 * name (#262), while the race leaderboard needed exactly that $200. Both scopes
 * are rendered under their own labels so the two can never be read as one.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { AgentPerformance } from "@/lib/api";

import { SessionKpis } from "./SessionKpis";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

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

const BASE: AgentPerformance = {
  agent_id: "agent.grid_1",
  session_num: 3,
  kind: "session",
  status: "running",
  realized_pnl: 120,
  unrealized_pnl: 0,
  total_pnl: 120,
  volume: 1200,
  fees: 0,
  trade_count: 3,
  win_rate: null,
  open_count: 1,
  closed_count: 3,
  executors: [],
};

function render(perf: AgentPerformance) {
  act(() => {
    root.render(<SessionKpis perf={perf} />);
  });
}

/** Every KPI in the card as `label → value`. */
function kpis(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const cell of Array.from(container.querySelectorAll("div.min-w-0"))) {
    const spans = cell.querySelectorAll("span");
    if (spans.length >= 2) out[spans[0].textContent ?? ""] = spans[1].textContent ?? "";
  }
  return out;
}

describe("SessionKpis scope labelling", () => {
  it("labels both scopes and names the instances each was summed over", () => {
    render({
      ...BASE,
      session_realized_pnl: 120,
      session_volume: 1200,
      session_instances: ["grid-20260810-000000"],
      lifetime_realized_pnl: 200,
      lifetime_volume: 2000,
      lifetime_instances: ["grid-20260801-000000", "grid-20260810-000000"],
    });

    const strip = kpis();
    expect(strip["Realized · session"]).toBe("+$120.00");
    expect(strip["Realized · lifetime"]).toBe("+$200.00");
    expect(strip["Volume · session"]).toBe("$1200.00");
    expect(strip["Volume · lifetime"]).toBe("$2000.00");
    // The audit trail: which generations each figure covers.
    expect(container.textContent).toContain("grid-20260801-000000");
    // The live book is single-scope and says so.
    expect(container.textContent).toContain("live only");
  });

  it("renders an unavailable session scope as unavailable, not as the lifetime total", () => {
    render({
      ...BASE,
      session_realized_pnl: null,
      session_volume: null,
      session_instances: [],
      lifetime_realized_pnl: 200,
      lifetime_volume: 2000,
      lifetime_instances: ["grid-20260801-000000"],
    });

    const strip = kpis();
    expect(strip["Realized · session"]).toBe("unavailable");
    expect(strip["Volume · session"]).toBe("unavailable");
    expect(strip["Realized · lifetime"]).toBe("+$200.00");
    expect(container.textContent).toContain("no ownership window");
  });

  it("shows no scope block for a direct-executor session, which has one scope", () => {
    render(BASE);

    expect(Object.keys(kpis())).not.toContain("Realized · session");
    expect(container.textContent).not.toContain("unavailable");
  });
});
