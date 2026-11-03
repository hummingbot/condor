/**
 * The read-only state of the Execute panel (ARCH-272).
 *
 * Two things make this overlay useful rather than merely obstructive: it names
 * the venue that is missing keys — the panel offers ~29 of them now, so "no API
 * keys" without a name is a riddle — and its CTA is a real destination, the
 * deep-linkable Settings → Keys and Wallets tab. Both are easy to lose in a
 * copy edit, so both are pinned here.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ViewOnlyOverlay } from "./ViewOnlyOverlay";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function render(connector: string) {
  act(() => {
    root.render(
      <MemoryRouter>
        <ViewOnlyOverlay connector={connector} />
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

describe("ViewOnlyOverlay", () => {
  it("names the venue it is blocking, in the same form the selector shows", () => {
    render("hyperliquid_perpetual");
    expect(container.textContent).toContain("View only — no API keys for Hyperliquid Perp");
  });

  it("says the market data behind it is still live", () => {
    render("binance");
    expect(container.textContent).toContain("Charts, order book and market data are live");
  });

  it("links to the Keys and Wallets tab, which is deep-linkable", () => {
    render("binance");
    const cta = container.querySelector("a")!;
    expect(cta.textContent).toBe("Add API keys");
    expect(cta.getAttribute("href")).toBe("/settings?tab=keys");
  });
});
