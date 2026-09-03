/**
 * Which rows of the scope picker are drawn emphasised, and which recede.
 *
 * A row that names a run — an agent, a bot, the bucket of executors no
 * controller claims — is a thing the reader compares against its siblings, and
 * draws semibold in full-contrast text. A controller or an executor is a detail
 * *of* one of those, and draws medium-weight muted.
 *
 * The group row is why this file exists: it arrived after the predicate was
 * written and was left out of it, so the one row summarising the most leaves
 * was the one that receded (READ-321). The predicate reads
 * `TOP_LEVEL_KINDS` now, and these cases pin both halves of it — the next kind
 * added is either in that set or it is not, and one of these two tests says so.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ScopeTree } from "./ScopeTree";
import type { ControllerInfo, ExecutorInfo } from "@/lib/api";
import { buildTree, leafFromController, leafFromExecutor } from "@/lib/perf-tree";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const controller = (bot: string, id: string): ControllerInfo =>
  ({
    controller_name: "pmm_simple",
    controller_id: id,
    bot_name: bot,
    status: "running",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 10,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 10,
    global_pnl_pct: 2,
    volume_traded: 100,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: "2026-09-01T08:00:00Z",
    config: {},
  }) as ControllerInfo;

/** An executor opened by hand: no bot behind it, so it lands in a `grp:` row. */
const looseExecutor = (id: string): ExecutorInfo =>
  ({
    id,
    type: "position_executor",
    connector: "binance",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "active",
    close_type: "",
    pnl: 3,
    volume: 50,
    timestamp: Date.parse("2026-09-01T09:00:00Z") / 1000,
    controller_id: "main",
    cum_fees_quote: 0,
    net_pnl_pct: 0.01,
    entry_price: 1,
    current_price: 1,
    close_timestamp: 0,
    custom_info: {},
    config: {},
  }) as ExecutorInfo;

const tree = () =>
  buildTree(
    [
      leafFromController(controller("hummingbot-alpha-1", "pmm_1")),
      leafFromExecutor(looseExecutor("exec-a")),
      leafFromExecutor(looseExecutor("exec-b")),
    ],
    "All",
    { groupByBot: true },
  );

let container: HTMLDivElement;
let root: Root;

/** The class list of the name span on the row whose visible name is `name`. */
function nameClass(name: string): string {
  const span = [...container.querySelectorAll<HTMLElement>("span")].find(
    (el) => el.className.includes("text-[11px]") && el.textContent === name,
  );
  if (!span) throw new Error(`no row named ${name}: ${container.textContent}`);
  return span.className;
}

function draw() {
  act(() => {
    root.render(
      <ScopeTree
        root={tree()}
        // Nothing selected: `active` would emphasise a row on its own and hide
        // the very difference these cases are about.
        activeId="all"
        open={new Set(["all", "bot:hummingbot-alpha-1", "grp:main"])}
        onSelect={() => {}}
        onToggleOpen={() => {}}
        cv={(v) => v}
        currencySymbol="$"
        now={Date.parse("2026-09-01T12:00:00Z")}
      />,
    );
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  draw();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("ScopeTree row emphasis", () => {
  it("draws the unclaimed-executor group with the same weight and colour as a bot", () => {
    expect(nameClass("main")).toBe(nameClass("hummingbot-alpha-1"));
    expect(nameClass("main")).toContain("font-semibold");
    expect(nameClass("main")).toContain("text-[var(--color-text)]");
  });

  it("still lets the rows nested under them recede", () => {
    for (const nested of ["pmm_1", "exec-a"]) {
      expect(nameClass(nested)).toContain("font-medium");
      expect(nameClass(nested)).toContain("text-[var(--color-text-muted)]");
      expect(nameClass(nested)).not.toBe(nameClass("hummingbot-alpha-1"));
    }
  });
});
