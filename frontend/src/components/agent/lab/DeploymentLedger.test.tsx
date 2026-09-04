/**
 * The ledger has to be readable as a claim, not just as a table.
 *
 * Two of its promises are only observable in the DOM: a run that deployed
 * nothing must *say so* rather than render an empty table frame, and a bot this
 * run released must read **closed** even though the instance is still deployed
 * and its performance snapshot still says "running". Both are the kind of thing
 * a pure-function test cannot see, so they are pinned here.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DeploymentLedger } from "./DeploymentLedger";
import type { DeploymentRow } from "@/lib/api";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function row(over: Partial<DeploymentRow> = {}): DeploymentRow {
  return {
    kind: "bot",
    label: "ema_trend_loop",
    detail: "deployed",
    created_tick: null,
    started_at: 1_786_052_371,
    ended_at: null,
    live: true,
    pnl: 0,
    volume: 0,
    scope: "bot:ema_trend_loop-20260806-213931",
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

async function render(rows: DeploymentRow[]) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <DeploymentLedger rows={rows} />
      </MemoryRouter>,
    );
  });
}

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

describe("DeploymentLedger", () => {
  it("says a run deployed nothing instead of framing an empty table", async () => {
    await render([]);
    expect(container.textContent).toContain("This run deployed nothing");
    expect(container.querySelector("table")).toBeNull();
  });

  it("lists a bot its ledger records, with a dash for a run that kept no log", async () => {
    await render([row()]);
    expect(container.textContent).toContain("ema_trend_loop");
    expect(container.textContent).toContain("deployed");
    expect(container.textContent).toContain("—");
    expect(container.textContent).not.toContain("tick 0");
  });

  it("reads closed, with an end time, for a bot released mid-run", async () => {
    await render([row({ live: false, ended_at: 1_786_088_056 })]);
    expect(container.textContent).toContain("closed");
    expect(container.textContent).toContain("→");
  });

  it("links each row into the fleet at its own address", async () => {
    await render([
      row(),
      row({ kind: "executor", label: "grid SOL-USDC", scope: "exec:e1" }),
    ]);
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toEqual([
      "/bots?scope=bot%3Aema_trend_loop-20260806-213931",
      "/bots?scope=exec%3Ae1",
    ]);
  });

  it("names the tick that created a row when the join found one", async () => {
    await render([row({ created_tick: 10 })]);
    expect(container.textContent).toContain("tick 10");
  });
});
