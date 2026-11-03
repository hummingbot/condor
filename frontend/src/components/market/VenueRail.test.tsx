/**
 * The venue rail's credentials split (ARCH-272), and the one rule that makes it
 * safe to put a venue selector inside the market browser at all.
 *
 * The trade panel offers every chartable venue, so the rail has to say which of
 * them can be traded — but only where that is news. On a server whose every
 * venue is credentialed the list must look like one plain list, with no group
 * headers appearing out of nowhere; and a header must never be drawn over an
 * empty run. Both directions are pinned here because the failure mode of the
 * second one is a visible header with nothing under it.
 *
 * These assertions came over from the top bar's exchange dropdown, which the
 * rail replaced: the grouping is the part of that control worth keeping.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VenueRail } from "./VenueRail";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

const CONNECTORS = ["hyperliquid_perpetual", "binance", "kraken"];

function render(props: {
  connectors?: string[];
  credentialed?: ReadonlySet<string>;
  value?: string;
  current?: string;
  onChange?: (v: string) => void;
}) {
  act(() => {
    root.render(
      <VenueRail
        connectors={props.connectors ?? CONNECTORS}
        credentialed={props.credentialed}
        value={props.value ?? "hyperliquid_perpetual"}
        current={props.current ?? "hyperliquid_perpetual"}
        onChange={props.onChange ?? (() => {})}
      />,
    );
  });
}

/** Group headers, in DOM order — the `aria-label` is on the group they head. */
const groups = () =>
  [...container.querySelectorAll('[role="group"]')].map((g) => g.getAttribute("aria-label"));

const optionLabels = () =>
  [...container.querySelectorAll('[role="option"]')].map((o) => o.textContent);

const headings = () =>
  [...container.querySelectorAll('[role="group"] > div')].map((d) => d.textContent);

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

describe("VenueRail grouping", () => {
  it("heads the two groups when the list is mixed, credentialed first", () => {
    render({ credentialed: new Set(["hyperliquid_perpetual"]) });
    expect(groups()).toEqual(["Your accounts", "View only"]);
    expect(headings()).toEqual(["Your accounts", "View only"]);
    expect(optionLabels()).toEqual(["Hyperliquid Perp", "Binance", "Kraken"]);
  });

  it("draws no header when every venue is credentialed", () => {
    render({ credentialed: new Set(CONNECTORS) });
    expect(headings()).toEqual([]);
    expect(optionLabels()).toHaveLength(3);
  });

  it("draws no header when every venue is view-only", () => {
    render({ credentialed: new Set<string>() });
    expect(headings()).toEqual([]);
    expect(optionLabels()).toHaveLength(3);
  });

  it("renders flat while the credentials answer is still pending", () => {
    // `credentialed` is undefined until the venues query resolves; flashing
    // every venue as view-only for one paint would be a lie.
    render({});
    expect(headings()).toEqual([]);
    expect(optionLabels()).toHaveLength(3);
  });

  it("names the axis only to a screen reader, never as a visible label", () => {
    // The column of exchange names does not need a word saying so; the group
    // headers are the only visible text above it, and the listbox keeps the
    // name for anyone who cannot see the list.
    render({ credentialed: new Set(["hyperliquid_perpetual"]) });
    expect(container.textContent).not.toContain("Exchange");
    expect(
      container.querySelector('[role="listbox"]')!.getAttribute("aria-label"),
    ).toBe("Exchange");
  });

  it("selects the same way from either group", () => {
    const onChange = vi.fn();
    render({ credentialed: new Set(["hyperliquid_perpetual"]), onChange });
    const viewOnly = [...container.querySelectorAll('[role="option"]')].find(
      (o) => o.textContent === "Binance",
    ) as HTMLButtonElement;
    act(() => viewOnly.click());
    expect(onChange).toHaveBeenCalledWith("binance");
  });
});

describe("the two selections the rail carries", () => {
  it("tells the browsed venue apart from the one on the chart", () => {
    // Browsing Kraken while the chart sits on Binance is the normal state of
    // this control, so the two must never be rendered as one.
    render({ value: "kraken", current: "binance" });
    const option = (label: string) =>
      [...container.querySelectorAll('[role="option"]')].find(
        (o) => o.textContent === label,
      )!;

    expect(option("Kraken").getAttribute("aria-selected")).toBe("true");
    expect(option("Kraken").getAttribute("aria-current")).toBeNull();
    expect(option("Binance").getAttribute("aria-selected")).toBe("false");
    expect(option("Binance").getAttribute("aria-current")).toBe("true");
  });
});
