/**
 * The session server chip's menu is portalled, not `absolute` (ARCH-260).
 *
 * The chip lives in the chat workspace's identity strip, and that workspace's
 * `main` is `overflow-hidden` (AppShell) — an `absolute right-0 top-full` panel
 * there is clipped at the strip's edge, and unlike a scroll container there is
 * no scroll position that brings the clipped rows back. Worse, the menu paired
 * that panel with a `fixed inset-0 z-40` backdrop covering the whole viewport:
 * while it was open, the user's next click anywhere on the page only closed the
 * menu, and the control they aimed at never saw the event.
 *
 * These cases pin the properties a refactor of the menu's contents must not
 * lose: the panel is a child of `document.body` at fixed coordinates, no
 * backdrop element stands between the user and the page, and one outside
 * mousedown both dismisses the menu and leaves the click to land on its target.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionServerChip } from "./SessionServerChip";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SERVERS = [
  { name: "alpha", online: true },
  { name: "beta", online: true },
];

vi.mock("@/lib/api", () => ({
  api: { getServers: vi.fn(async () => SERVERS) },
}));

let container: HTMLDivElement;
let root: Root;
let picked: string[];

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <SessionServerChip
          serverName="alpha"
          onSelect={(name) => picked.push(name)}
        />
      </QueryClientProvider>,
    );
  });
}

const trigger = () => container.querySelector("button")!;

/**
 * jsdom lays nothing out, so every rect is 0×0 at the origin — which would put
 * a right-aligned panel's edge at the far right of the window and leave it no
 * room at all. Stand the trigger where it actually sits: near the right end of
 * the chat identity strip.
 */
function placeTrigger() {
  trigger().getBoundingClientRect = () =>
    ({ top: 20, bottom: 40, left: 800, right: 900, width: 100, height: 20 }) as DOMRect;
}

/** Let the servers query settle so the menu has rows to show. */
const flush = () => act(async () => void (await new Promise((r) => setTimeout(r, 0))));
/** The option row, wherever in the document it ended up. */
const option = (label: string) =>
  Array.from(document.querySelectorAll("button")).find(
    (b) => b.textContent?.trim() === label,
  );
/** Walk up from an option row to the element `document.body` owns directly. */
const panel = () => {
  let node: HTMLElement | null = option("beta") ?? null;
  while (node && node.parentElement !== document.body) node = node.parentElement;
  return node;
};

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  picked = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("SessionServerChip menu placement", () => {
  it("renders the open panel outside the trigger's subtree, right-aligned", async () => {
    await render();
    placeTrigger();
    await click(trigger());
    await flush();

    const p = panel();
    expect(p).toBeTruthy();
    // Portalled: `main`'s `overflow-hidden` on the chat workspace cannot clip it.
    expect(p!.parentElement).toBe(document.body);
    expect(container.contains(p!)).toBe(false);
    expect(p!.style.position).toBe("fixed");
    // align="right" pins the panel's right edge, clamped inside the viewport.
    expect(parseFloat(p!.style.right)).toBeGreaterThanOrEqual(8);
    expect(p!.style.left).toBe("");
    // Height and width caps travel inline, so a long server list scrolls in the
    // panel rather than running off the window.
    expect(parseFloat(p!.style.maxHeight)).toBeGreaterThan(0);
    expect(parseFloat(p!.style.maxWidth)).toBeGreaterThan(0);
    expect(p!.getAttribute("role")).toBe("listbox");
  });

  it("has no full-viewport backdrop to swallow the next click", async () => {
    await render();
    placeTrigger();
    await click(trigger());
    await flush();
    expect(panel()).toBeTruthy();

    // The backdrop this replaced covered the page above all chrome, so the
    // click that dismissed the menu never reached its target.
    const backdrops = Array.from(document.querySelectorAll("div")).filter((d) =>
      d.className.includes("fixed inset-0"),
    );
    expect(backdrops).toEqual([]);

    const outside = document.createElement("button");
    let hits = 0;
    outside.addEventListener("click", () => hits++);
    document.body.appendChild(outside);
    await click(outside);

    // One click: the menu closes *and* the button underneath fires.
    expect(panel()).toBeNull();
    expect(hits).toBe(1);
    outside.remove();
  });

  it("announces its state on the trigger and closes after a pick", async () => {
    await render();
    expect(trigger().getAttribute("aria-haspopup")).toBe("listbox");
    expect(trigger().getAttribute("aria-expanded")).toBe("false");

    placeTrigger();
    await click(trigger());
    await flush();
    expect(trigger().getAttribute("aria-expanded")).toBe("true");

    await click(option("beta")!);
    expect(picked).toEqual(["beta"]);
    expect(panel()).toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
  });
});
