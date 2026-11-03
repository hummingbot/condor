/**
 * What the "Unattached" row says it is holding.
 *
 * The row used to read "1 controller left no record", which is a history the
 * record cannot support: the ids it buckets by are not controllers. `main` is
 * the trading API's default for an executor created without one — every
 * position opened through Condor's browser lands there, because the create
 * route sends no id at all — and an MCP-created position carries the calling
 * agent's session id. Neither is a deployment that ran and then lost its
 * record, so the old subtitle sent the reader hunting one that never existed
 * (CORR-362).
 *
 * These cases pin the replacement: the row counts the executors folded beneath
 * it, which is a number the buckets below add up to, and it claims nothing
 * about a controller beyond the one thing that is true of every leaf under it
 * — that none of them is filed under one.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ScopeTree } from "./ScopeTree";
import type { ExecutorInfo } from "@/lib/api";
import { buildTree, leafFromExecutor, type PerfNode } from "@/lib/perf-tree";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/**
 * An executor with no bot behind it, tagged with whatever `controller_id` the
 * server stamped on it — `main` for one opened from the browser, a session id
 * for one an agent opened over MCP.
 */
const looseExecutor = (id: string, controllerId: string): ExecutorInfo =>
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
    controller_id: controllerId,
    cum_fees_quote: 0,
    net_pnl_pct: 0.01,
    entry_price: 1,
    current_price: 1,
    close_timestamp: 0,
    custom_info: {},
    config: {},
  }) as ExecutorInfo;

const treeOf = (executors: ExecutorInfo[]): PerfNode =>
  buildTree(
    executors.map((e) => leafFromExecutor(e)),
    "All",
    { grouping: ["bot"] },
  );

let container: HTMLDivElement;
let root: Root;

function draw(tree: PerfNode) {
  act(() => {
    root.render(
      <ScopeTree
        root={tree}
        activeId="all"
        open={new Set(["all", "orphans"])}
        onSelect={() => {}}
        onToggleOpen={() => {}}
        cv={(v) => v}
        currencySymbol="$"
        now={Date.parse("2026-09-01T12:00:00Z")}
      />,
    );
  });
}

/** The subtitle line under the row whose visible name is `name`. */
function subtitleOf(name: string): string {
  const label = [...container.querySelectorAll<HTMLElement>("span")].find(
    (el) => el.className.includes("text-[11px]") && el.textContent === name,
  );
  const row = label?.closest("button");
  const sub = row && [...row.querySelectorAll<HTMLElement>("span")].find((el) => el.className === "truncate");
  if (!sub) throw new Error(`no subtitle on a row named ${name}: ${container.textContent}`);
  return sub.textContent ?? "";
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

describe("the Unattached row's subtitle", () => {
  it("counts the executors it gathers, not controllers", () => {
    draw(treeOf([looseExecutor("exec-a", "main"), looseExecutor("exec-b", "main")]));
    expect(subtitleOf("Unattached")).toBe("2 executors · under no controller");
  });

  it("claims no controller behind a single position opened by hand", () => {
    draw(treeOf([looseExecutor("exec-a", "main")]));
    const subtitle = subtitleOf("Unattached");
    expect(subtitle).toBe("1 executor · under no controller");
    // The whole of the defect: a row standing for one hand-opened position
    // asserting that a controller existed and then lost its record.
    expect(subtitle).not.toMatch(/controllers? left no record/);
    expect(subtitle).not.toMatch(/\d+ controller/);
  });

  // An executor an agent opened over MCP carries that agent's session id, and
  // one left behind by a stopped deployment carries its controller's — both
  // land in this row under buckets of their own. Neither is a controller the
  // tree can show, so the row counts them the same way rather than telling one
  // of the two populations a story about the other.
  it("counts across the ids it buckets by, whatever those ids name", () => {
    draw(
      treeOf([
        looseExecutor("exec-a", "main"),
        looseExecutor("exec-b", "session-7f21"),
        looseExecutor("exec-c", "pmm_1"),
      ]),
    );
    expect(subtitleOf("Unattached")).toBe("3 executors · under no controller");
    // The ids themselves are untouched: each is still a row, still named by
    // the one name its executors carry, still counted in executors.
    expect(subtitleOf("main")).toBe("1 executor");
    expect(subtitleOf("session-7f21")).toBe("1 executor");
  });
});
