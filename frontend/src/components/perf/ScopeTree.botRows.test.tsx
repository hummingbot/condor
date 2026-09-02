/**
 * The bot level of the scope picker, and the Stop button that is the reason
 * it came back.
 *
 * Stopping a bot used to be reachable only from the report header, and only
 * once the bubbles had narrowed the fleet to one bot — a filter interaction
 * standing in for a selection, which readers did not find. These cases pin the
 * two halves of the fix at the row: a bot is a row you can select, and it
 * carries its own verb beside its name, on a control that is *not* the one that
 * selects it.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ScopeTree } from "./ScopeTree";
import type { ControllerInfo } from "@/lib/api";
import { buildTree, leafFromController, type PerfNode } from "@/lib/perf-tree";

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

const tree = () =>
  buildTree(
    [
      leafFromController(controller("hummingbot-alpha-1", "pmm_1")),
      leafFromController(controller("hummingbot-alpha-1", "pmm_2")),
      leafFromController(controller("beta", "grid_1")),
    ],
    "All",
    { groupByBot: true },
  );

let container: HTMLDivElement;
let root: Root;

function render(node: React.ReactNode) {
  act(() => {
    root.render(node);
  });
}

/** Every row's visible name, in the order the picker draws them. */
const rowNames = () =>
  [...container.querySelectorAll<HTMLElement>("span")]
    .filter((el) => el.className.includes("text-[11px]") && !el.className.includes("tabular-nums"))
    .map((el) => el.textContent ?? "");

const stopButtons = () => [
  ...container.querySelectorAll<HTMLButtonElement>('button[aria-label^="Stop "]'),
];

function draw(over: Partial<React.ComponentProps<typeof ScopeTree>> = {}) {
  render(
    <ScopeTree
      root={tree()}
      activeId="all"
      open={new Set(["all", "bot:hummingbot-alpha-1", "bot:beta"])}
      onSelect={() => {}}
      onToggleOpen={() => {}}
      cv={(v) => v}
      currencySymbol="$"
      now={Date.parse("2026-09-01T12:00:00Z")}
      {...over}
    />,
  );
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

describe("ScopeTree bot rows", () => {
  it("draws a row per bot with its controllers underneath", () => {
    draw();
    expect(rowNames()).toEqual(["hummingbot-alpha-1", "pmm_1", "pmm_2", "beta", "grid_1"]);
  });

  // The Stop button posts this name to the API, so it has to be the bot's own
  // and not whatever the row had room to draw (`shortBotName` trims a doubled
  // timestamp suffix, which a real deployment name carries).
  it("hands the action the bot's full name", () => {
    const seen: string[] = [];
    draw({
      renderAction: (node: PerfNode) => {
        if (node.kind !== "bot") return null;
        seen.push(node.label);
        return <button aria-label={`Stop ${node.label}`}>stop</button>;
      },
    });
    expect(seen).toEqual(["hummingbot-alpha-1", "beta"]);
    expect(stopButtons()).toHaveLength(2);
  });

  it("does not select the row when the row's own verb is clicked", () => {
    const onSelect = vi.fn();
    const onStop = vi.fn();
    draw({
      onSelect,
      renderAction: (node: PerfNode) =>
        node.kind === "bot" ? (
          <button aria-label={`Stop ${node.label}`} onClick={() => onStop(node.label)}>
            stop
          </button>
        ) : null,
    });

    act(() => stopButtons()[0].click());
    expect(onStop).toHaveBeenCalledWith("hummingbot-alpha-1");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("offers no action on a controller or executor row", () => {
    const kinds: string[] = [];
    draw({ renderAction: (node: PerfNode) => (kinds.push(node.kind), null) });
    expect(new Set(kinds)).toEqual(new Set(["bot", "controller"]));
  });
});
