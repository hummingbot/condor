/**
 * The favourites strip's two load-bearing promises.
 *
 * A star belongs to the server it was made on — two servers may both list
 * `binance`, but they are different accounts, and offering one server's watch
 * list while working on the other is the bleed this scoping exists to stop. And
 * a chip has to carry its own venue back to the trade surface, because the
 * strip shows stars from every venue on the server, not just the one on screen.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { MARKET_FAVORITES_KEY } from "@/lib/sessionState";
import { FavoritesStrip } from "./FavoritesStrip";
import type { MarketPick } from "./MarketBrowser";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
const picked: MarketPick[] = [];

function seed(favorites: { server: string; connector: string; pair: string }[]) {
  localStorage.setItem(MARKET_FAVORITES_KEY, JSON.stringify(favorites));
}

async function render(server: string, connector = "binance", pair = "BTC-USDT") {
  await act(async () => {
    root.render(
      <FavoritesStrip
        server={server}
        connector={connector}
        pair={pair}
        onPick={(m) => picked.push(m)}
      />,
    );
  });
}

/** The chip buttons, in order — the unstar buttons carry no pair text. */
function chips(): HTMLElement[] {
  return [...container.querySelectorAll("button")].filter((b) =>
    /-/.test(b.textContent ?? ""),
  ) as HTMLElement[];
}

/** The draggable chip wrappers — one per favourite, in strip order. */
function chipRow(index: number): HTMLElement {
  return container.querySelectorAll("span[draggable]")[index] as HTMLElement;
}

function altArrow(el: HTMLElement, key: string, altKey = true) {
  el.dispatchEvent(
    new KeyboardEvent("keydown", { key, altKey, bubbles: true, cancelable: true }),
  );
}

/**
 * jsdom implements neither DragEvent nor DataTransfer, so the three events the
 * strip listens for are dispatched by hand with a stub attached.
 */
function drag(from: HTMLElement, to: HTMLElement) {
  const dataTransfer = {
    effectAllowed: "",
    dropEffect: "",
    setData: () => {},
    getData: () => "",
  };
  for (const [el, type] of [
    [from, "dragstart"],
    [to, "dragover"],
    [to, "drop"],
    [from, "dragend"],
  ] as const) {
    const e = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(e, "dataTransfer", { value: dataTransfer });
    el.dispatchEvent(e);
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  picked.length = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("FavoritesStrip", () => {
  it("shows only the stars made on the server it is rendered for", async () => {
    seed([
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
      { server: "beta", connector: "binance", pair: "DOGE-USDT" },
    ]);

    await render("alpha");
    expect(chips().map((c) => c.textContent)).toEqual(["SOL-USDC"]);

    await render("beta");
    expect(chips().map((c) => c.textContent)).toEqual(["DOGE-USDT"]);
  });

  it("carries a chip's own venue back, not the one on screen", async () => {
    seed([{ server: "alpha", connector: "kucoin", pair: "SOL-USDC" }]);
    await render("alpha", "binance");

    await act(async () => chips()[0].click());
    expect(picked).toEqual([{ connector: "kucoin", pair: "SOL-USDC" }]);
  });

  it("renders nothing when this server has no stars", async () => {
    seed([{ server: "beta", connector: "binance", pair: "SOL-USDC" }]);
    await render("alpha");
    expect(container.textContent).toBe("");
  });

  it("adopts pre-upgrade stars, which carried no server", async () => {
    // What was on disk before the key grew a `server` field.
    seed([{ connector: "binance", pair: "SOL-USDC" }] as never);

    await render("alpha");
    expect(chips().map((c) => c.textContent)).toEqual(["SOL-USDC"]);
    expect(JSON.parse(localStorage.getItem(MARKET_FAVORITES_KEY)!)).toEqual([
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
    ]);

    // Adopted once and for all: the next server does not inherit them too.
    await render("beta");
    expect(chips()).toEqual([]);
  });

  it("reorders with Alt+arrow, leaving other servers' lists alone", async () => {
    seed([
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
      { server: "beta", connector: "binance", pair: "DOGE-USDT" },
      { server: "alpha", connector: "binance", pair: "BTC-USDT" },
    ]);
    await render("alpha");
    expect(chips().map((c) => c.textContent)).toEqual(["SOL-USDC", "BTC-USDT"]);

    await act(async () => altArrow(chips()[1], "ArrowLeft"));
    expect(chips().map((c) => c.textContent)).toEqual(["BTC-USDT", "SOL-USDC"]);

    // beta's star kept both its order and its slot in the flat store.
    expect(JSON.parse(localStorage.getItem(MARKET_FAVORITES_KEY)!)).toEqual([
      { server: "alpha", connector: "binance", pair: "BTC-USDT" },
      { server: "beta", connector: "binance", pair: "DOGE-USDT" },
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
    ]);
  });

  it("does not walk a chip off either end", async () => {
    seed([
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
      { server: "alpha", connector: "binance", pair: "BTC-USDT" },
    ]);
    await render("alpha");

    await act(async () => altArrow(chips()[0], "ArrowLeft"));
    await act(async () => altArrow(chips()[1], "ArrowRight"));
    expect(chips().map((c) => c.textContent)).toEqual(["SOL-USDC", "BTC-USDT"]);
  });

  it("leaves a bare arrow to the strip's own scrolling", async () => {
    seed([
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
      { server: "alpha", connector: "binance", pair: "BTC-USDT" },
    ]);
    await render("alpha");

    await act(async () => altArrow(chips()[1], "ArrowLeft", false));
    expect(chips().map((c) => c.textContent)).toEqual(["SOL-USDC", "BTC-USDT"]);
  });

  it("reorders on a drag and drop", async () => {
    seed([
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
      { server: "alpha", connector: "binance", pair: "BTC-USDT" },
      { server: "alpha", connector: "binance", pair: "ETH-USDT" },
    ]);
    await render("alpha");

    // Drag the last chip onto the first.
    await act(async () => drag(chipRow(2), chipRow(0)));
    expect(chips().map((c) => c.textContent)).toEqual([
      "ETH-USDT",
      "SOL-USDC",
      "BTC-USDT",
    ]);
  });

  it("unstars from the strip, dropping the chip", async () => {
    seed([
      { server: "alpha", connector: "binance", pair: "SOL-USDC" },
      { server: "alpha", connector: "binance", pair: "BTC-USDT" },
    ]);
    await render("alpha");

    const unstar = container.querySelector(
      '[aria-label^="Unstar SOL-USDC"]',
    ) as HTMLElement;
    await act(async () => unstar.click());

    expect(chips().map((c) => c.textContent)).toEqual(["BTC-USDT"]);
    // The other server's list is untouched by an unstar over here.
    expect(JSON.parse(localStorage.getItem(MARKET_FAVORITES_KEY)!)).toEqual([
      { server: "alpha", connector: "binance", pair: "BTC-USDT" },
    ]);
  });
});
