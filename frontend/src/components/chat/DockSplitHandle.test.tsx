/**
 * The seam inside a dock — where the reader put it, and that it stays there.
 *
 * The desk's two sections opened at an even split and could only be even: the
 * one answer to "I want the execution table taller" was to collapse the
 * portfolio away entirely. What is pinned here is the drag that replaced that:
 * the fraction it computes against the two neighbours it measures, the floor
 * that keeps a section from vanishing under it, and that the number survives a
 * remount — because a split you have to set again on every reload is not a
 * setting.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useDockSplit } from "@/hooks/useDockSplit";

import { DockSplitHandle } from "./DockSplitHandle";

const KEY = "condor.test.split";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

/**
 * The two panes and the seam between them, as `AccountDock` composes them.
 *
 * The handle measures its own siblings, so the harness has to be the real
 * shape: a pane, the separator, a pane. jsdom lays nothing out, so the sizes it
 * would read are stubbed after the mount — 200px each, the top of the pair at
 * y=100, which is the frame every assertion below is written against.
 */
function Split() {
  const { frac, setFrac, defaultFrac } = useDockSplit(KEY);
  return (
    <div>
      <div data-testid="top" style={{ flexGrow: frac }} />
      <DockSplitHandle
        frac={frac}
        setFrac={setFrac}
        defaultFrac={defaultFrac}
        label="Resize sections"
      />
      <div data-testid="bottom" style={{ flexGrow: 1 - frac }} />
    </div>
  );
}

const pane = (id: string) =>
  container.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
const seam = () => container.querySelector<HTMLElement>('[role="separator"]')!;
/** What the pane actually grows by — the number the drag is for. */
const grow = (id: string) => Number(pane(id).style.flexGrow);

async function render() {
  await act(async () => {
    root.render(<Split />);
  });
  for (const [id, top] of [
    ["top", 100],
    ["bottom", 300],
  ] as const) {
    Object.defineProperty(pane(id), "offsetHeight", {
      value: 200,
      configurable: true,
    });
    pane(id).getBoundingClientRect = () =>
      ({ top, bottom: top + 200, height: 200 }) as DOMRect;
  }
}

/** Grab the seam and let go at `clientY`. */
async function drag(clientY: number) {
  await act(async () => {
    seam().dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, clientY: 300 }),
    );
  });
  await act(async () => {
    document.dispatchEvent(new MouseEvent("mousemove", { clientY }));
  });
  await act(async () => {
    document.dispatchEvent(new MouseEvent("mouseup"));
  });
}

async function press(key: string) {
  await act(async () => {
    seam().dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key }));
  });
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

describe("the seam between two dock sections", () => {
  it("opens even, and moves where the pointer let go", async () => {
    await render();
    expect(grow("top")).toBe(0.5);

    // 400px of pane starting at y=100; letting go at 400 leaves 300 above.
    await drag(400);
    expect(grow("top")).toBeCloseTo(0.75);
    expect(grow("bottom")).toBeCloseTo(0.25);
  });

  it("keeps a header and a row in the section being squeezed", async () => {
    await render();

    // Dragged clean off the top of the panel: the pane above still keeps its
    // 96px floor, because a section collapsed to nothing is the toggle's job
    // and not something a drag should be able to do by accident.
    await drag(-500);
    expect(grow("top")).toBeCloseTo(96 / 400);

    await drag(5000);
    expect(grow("top")).toBeCloseTo((400 - 96) / 400);
  });

  it("remembers the split, and gives it back on the next mount", async () => {
    await render();
    await drag(400);
    expect(localStorage.getItem(KEY)).toBe("0.75");

    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    expect(grow("top")).toBeCloseTo(0.75);
  });

  it("steps with the arrow keys and resets on a double-click", async () => {
    await render();

    // Down grows the section above — the seam moves the way the arrow points.
    await press("ArrowDown");
    expect(grow("top")).toBeCloseTo(0.52);
    await press("ArrowUp");
    await press("ArrowUp");
    expect(grow("top")).toBeCloseTo(0.48);

    await act(async () => {
      seam().dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    });
    expect(grow("top")).toBe(0.5);
  });

  it("reads a stored split back inside the envelope it allows", async () => {
    // A hand-edited or stale value cannot hand one section the whole panel.
    localStorage.setItem(KEY, "0.99");
    await render();
    expect(grow("top")).toBeCloseTo(0.85);

    await act(async () => root.unmount());
    root = createRoot(container);
    localStorage.setItem(KEY, "not a number");
    await render();
    expect(grow("top")).toBe(0.5);
  });
});
