/**
 * A host with no stop path gets a read-only executor table (ARCH-391).
 *
 * `ExecutorTable` and `DetailPanel` used to require `onStop`, so the agent
 * run view's `SessionExecutors` passed `() => {}` and a live run drew red Stop
 * buttons that silently did nothing. The stop contract is now optional: without
 * `onStop` neither the row nor the panel draws a Stop control, and with it
 * (ExecutorRows, PerfBrowser) both still call it with the executor id.
 *
 * The selection column is optional the same way (ARCH-420): the four selection
 * props travel together, and a host that passes none — as the agent run view's
 * `SessionExecutors` now does — gets no checkbox cells at all, rather than a
 * column whose ticks nothing reads. `ExecutorRows` still passes them, because
 * its bulk Stop and CSV export are what selection is for.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type ExecutorInfo } from "@/lib/api";

vi.mock("@/components/charts/ExecutorChart", () => ({
  ExecutorChart: () => null,
}));
vi.mock("@/components/executor/PairLabel", () => ({
  PairLabel: ({ tradingPair }: { tradingPair: string }) => <span>{tradingPair}</span>,
}));

import { DetailPanel, ExecutorTable } from "./ExecutorTable";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const active: ExecutorInfo = {
  id: "ex-1",
  type: "position_executor",
  connector: "binance",
  trading_pair: "SOL-USDC",
  side: "BUY",
  status: "active",
  close_type: "",
  pnl: 1.5,
  volume: 100,
  timestamp: 1_700_000_000,
  controller_id: "ctrl",
  cum_fees_quote: 0.1,
  net_pnl_pct: 0.01,
  entry_price: 150,
  current_price: 151,
  close_timestamp: 0,
  custom_info: {},
  config: {},
};

let container: HTMLDivElement;
let root: Root;

function render(node: React.ReactNode) {
  act(() => {
    root.render(<MemoryRouter>{node}</MemoryRouter>);
  });
}

const tableProps = {
  executors: [active],
  sortKey: "timestamp" as const,
  sortDir: "desc" as const,
  onSort: () => {},
  onRowClick: () => {},
  selectedExecutorId: null,
};

const checkboxes = () =>
  Array.from(container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]'));

const rowStop = () => container.querySelector<HTMLButtonElement>('button[title="Stop executor"]');
const panelStop = () =>
  Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === "Stop",
  );

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

describe("read-only executor table (no onStop)", () => {
  it("draws no Stop control on an active row", () => {
    render(<ExecutorTable {...tableProps} />);
    expect(container.textContent).toContain("SOL");
    expect(rowStop()).toBeNull();
  });

  it("draws no Stop button in the detail panel of an active executor", () => {
    render(<DetailPanel executor={active} server="local" onClose={() => {}} />);
    expect(container.querySelector('button[title="Close"]')).not.toBeNull();
    expect(panelStop()).toBeUndefined();
  });
});

describe("stoppable executor table (onStop given)", () => {
  it("row Stop calls onStop with the executor id", () => {
    const onStop = vi.fn();
    render(<ExecutorTable {...tableProps} onStop={onStop} stoppingIds={new Set()} />);
    const button = rowStop();
    expect(button).not.toBeNull();
    act(() => button!.click());
    expect(onStop).toHaveBeenCalledWith("ex-1");
  });

  it("panel Stop calls onStop with the executor id", () => {
    const onStop = vi.fn();
    render(<DetailPanel executor={active} server="local" onClose={() => {}} onStop={onStop} />);
    const button = panelStop();
    expect(button).toBeDefined();
    act(() => button!.click());
    expect(onStop).toHaveBeenCalledWith("ex-1");
  });
});

describe("selection column", () => {
  it("draws no checkbox at all without onToggleSelect", () => {
    render(<ExecutorTable {...tableProps} />);
    // The row itself still rendered — this is an absent column, not an absent table.
    expect(container.querySelector("tbody tr")).not.toBeNull();
    expect(checkboxes()).toHaveLength(0);
  });

  it("draws the header and row checkboxes when onToggleSelect is given", () => {
    render(
      <ExecutorTable
        {...tableProps}
        selectedIds={new Set<string>()}
        onToggleSelect={() => {}}
        onSelectAll={() => {}}
        allSelected={false}
      />,
    );
    expect(container.querySelectorAll('thead input[type="checkbox"]')).toHaveLength(1);
    expect(container.querySelectorAll('tbody input[type="checkbox"]')).toHaveLength(1);
  });

  it("row checkbox reports the executor id and reflects selectedIds", () => {
    const onToggleSelect = vi.fn();
    render(
      <ExecutorTable
        {...tableProps}
        selectedIds={new Set(["ex-1"])}
        onToggleSelect={onToggleSelect}
        onSelectAll={() => {}}
        allSelected
      />,
    );
    const row = container.querySelector<HTMLInputElement>('tbody input[type="checkbox"]')!;
    expect(row.checked).toBe(true);
    act(() => row.click());
    expect(onToggleSelect).toHaveBeenCalledWith("ex-1");
  });

  it("header checkbox calls onSelectAll", () => {
    const onSelectAll = vi.fn();
    render(
      <ExecutorTable
        {...tableProps}
        selectedIds={new Set<string>()}
        onToggleSelect={() => {}}
        onSelectAll={onSelectAll}
        allSelected={false}
      />,
    );
    const head = container.querySelector<HTMLInputElement>('thead input[type="checkbox"]')!;
    act(() => head.click());
    expect(onSelectAll).toHaveBeenCalled();
  });
});
