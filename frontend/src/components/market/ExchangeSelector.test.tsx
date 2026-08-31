/**
 * The venue list's credentials split (ARCH-272).
 *
 * The trade panel now offers every chartable venue, so the selector has to say
 * which of them can be traded — but only where that is news. On a server whose
 * every venue is credentialed the list must look exactly as it always did, with
 * no group headers appearing out of nowhere; and a header must never be drawn
 * over an empty run. Both directions are pinned here because the failure mode of
 * the second one is a visible header with nothing under it.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExchangeSelector } from "./ExchangeSelector";

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
  onChange?: (v: string) => void;
}) {
  act(() => {
    root.render(
      <ExchangeSelector
        connectors={props.connectors ?? CONNECTORS}
        credentialed={props.credentialed}
        value="hyperliquid_perpetual"
        onChange={props.onChange ?? (() => {})}
      />,
    );
  });
  // The menu is portalled to document.body, so open it from the trigger.
  act(() => {
    container.querySelector("button")!.click();
  });
}

/** Group headers, in DOM order — the `aria-label` is on the group they head. */
const groups = () =>
  [...document.querySelectorAll('[role="group"]')].map((g) => g.getAttribute("aria-label"));

const optionLabels = () =>
  [...document.querySelectorAll('[role="option"]')].map((o) => o.textContent);

const headings = () =>
  [...document.querySelectorAll('[role="group"] > div')].map((d) => d.textContent);

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

describe("ExchangeSelector grouping", () => {
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

  it("selects the same way from either group", () => {
    const onChange = vi.fn();
    render({ credentialed: new Set(["hyperliquid_perpetual"]), onChange });
    const viewOnly = [...document.querySelectorAll('[role="option"]')].find(
      (o) => o.textContent === "Binance",
    ) as HTMLButtonElement;
    act(() => viewOnly.click());
    expect(onChange).toHaveBeenCalledWith("binance");
  });
});
