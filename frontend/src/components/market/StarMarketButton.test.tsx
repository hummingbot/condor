/**
 * Starring the pair already on screen (the header toggle).
 *
 * The one thing it must not do is star it globally: the button sits in a header
 * that belongs to one server, so the entry it writes has to name that server or
 * the strip on the next server inherits it.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { MARKET_FAVORITES_KEY } from "@/lib/sessionState";
import { StarMarketButton } from "./StarMarketButton";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

async function render(server: string, connector = "binance", pair = "SOL-USDC") {
  await act(async () => {
    root.render(
      <StarMarketButton server={server} connector={connector} pair={pair} />,
    );
  });
}

function button(): HTMLButtonElement {
  return container.querySelector("button") as HTMLButtonElement;
}

function stored() {
  return JSON.parse(localStorage.getItem(MARKET_FAVORITES_KEY) ?? "[]");
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("StarMarketButton", () => {
  it("stars the header's market against the server it is rendered for", async () => {
    await render("alpha");
    expect(button().getAttribute("aria-pressed")).toBe("false");

    await act(async () => button().click());
    expect(stored()).toEqual([
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
    ]);
    expect(button().getAttribute("aria-pressed")).toBe("true");

    // The same market on another server is a separate, unstarred thing.
    await render("beta");
    expect(button().getAttribute("aria-pressed")).toBe("false");
  });

  it("unstars on a second press", async () => {
    await render("alpha");
    await act(async () => button().click());
    await act(async () => button().click());
    expect(stored()).toEqual([]);
  });

  it("renders nothing before a pair is chosen", async () => {
    await render("alpha", "binance", "");
    expect(container.textContent).toBe("");
  });
});
