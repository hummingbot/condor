/**
 * The "once" in a one-time hint (PR #224 QA).
 *
 * The hint exists because a bare `<kbd>/</kbd>` on the market chip taught
 * nobody what the key was for. Its whole value is that it says so in words and
 * then goes away — a tip that came back on every hover would be the permanent
 * visual noise it was added to avoid. So the cases that matter are the ones
 * about *retiring* it: a hover that reached the bubble spends the hint, a
 * hover that only passed over the control does not, and the flag outlives the
 * mount.
 *
 * Needs a DOM and a real `localStorage`, so this file overrides vitest's
 * default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HintBubble } from "@/components/ui/HintBubble";
import { useOneTimeHint } from "./useOneTimeHint";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const KEY = "condor.test.hint";

/** A control with the hint hung off it, exactly as a consumer wires it. */
function Harness() {
  const hint = useOneTimeHint(KEY, { delayMs: 400, holdMs: 4000 });
  return (
    <div>
      <button id="anchor" {...hint.hoverProps} title={hint.pending ? undefined : "Change market (/)"}>
        BTC-USDT
      </button>
      {hint.visible && <HintBubble>Tip: press / to browse markets.</HintBubble>}
    </div>
  );
}

let container: HTMLDivElement;
let root: Root;

const anchor = () => container.querySelector("#anchor") as HTMLButtonElement;
const bubble = () => container.querySelector("[role='status']");

function hover(where: "in" | "out") {
  // React has no listener for `pointerenter`/`pointerleave` — neither event
  // bubbles, so it synthesises both from the `pointerover`/`pointerout` pair it
  // delegates at the root. A null `relatedTarget` reads as "came from / went to
  // outside the document", which is what makes it an enter rather than a move
  // between two children. jsdom has no `PointerEvent`; a `MouseEvent` under the
  // same name reaches the same plugin.
  act(() => {
    anchor().dispatchEvent(
      new MouseEvent(where === "in" ? "pointerover" : "pointerout", {
        bubbles: true,
        relatedTarget: null,
      }),
    );
  });
}

/** Let the show delay (and optionally the hold) elapse. */
function elapse(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<Harness />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("useOneTimeHint", () => {
  it("teaches the shortcut once the pointer rests, then retires it", () => {
    hover("in");
    expect(bubble()).toBeNull(); // not on contact — this is a rest, not a flicker

    elapse(400);
    expect(bubble()?.textContent).toContain("press / to browse markets");
    // The browser's own tooltip stays out of the way while the bubble is up.
    expect(anchor().getAttribute("title")).toBeNull();

    elapse(4000);
    expect(bubble()).toBeNull();
    // ...and the durable affordance takes over from here.
    expect(anchor().getAttribute("title")).toBe("Change market (/)");

    hover("out");
    hover("in");
    elapse(400);
    expect(bubble()).toBeNull();
  });

  it("spends the hint on a hover that ends early, having shown it", () => {
    hover("in");
    elapse(400);
    expect(bubble()).not.toBeNull();

    hover("out");
    expect(bubble()).toBeNull();

    hover("in");
    elapse(400);
    expect(bubble()).toBeNull();
  });

  it("keeps the hint for a pointer that only passed over the control", () => {
    hover("in");
    elapse(200);
    hover("out");
    elapse(4000);
    expect(bubble()).toBeNull();
    expect(localStorage.getItem(KEY)).toBeNull();

    // The next real hover still gets taught.
    hover("in");
    elapse(400);
    expect(bubble()).not.toBeNull();
  });

  it("remembers across mounts", () => {
    hover("in");
    elapse(400);
    expect(localStorage.getItem(KEY)).toBe("1");

    act(() => root.unmount());
    root = createRoot(container);
    act(() => root.render(<Harness />));

    hover("in");
    elapse(400);
    expect(bubble()).toBeNull();
    // A remounted hint that was already taught wears its title from the start.
    expect(anchor().getAttribute("title")).toBe("Change market (/)");
  });

  it("retires unshown when the shortcut is used", () => {
    // `markTaught` is the "they already know" path — the `/` keydown calls it.
    function Used() {
      const hint = useOneTimeHint(KEY, { delayMs: 400 });
      return (
        <div>
          <button id="anchor" {...hint.hoverProps} onClick={hint.markTaught}>
            BTC-USDT
          </button>
          {hint.visible && <HintBubble>Tip</HintBubble>}
        </div>
      );
    }
    act(() => root.render(<Used />));
    act(() => anchor().dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(localStorage.getItem(KEY)).toBe("1");

    hover("in");
    elapse(400);
    expect(bubble()).toBeNull();
  });
});
