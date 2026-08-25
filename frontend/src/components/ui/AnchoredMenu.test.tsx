/**
 * The focus contract of the portalled menu (CORR-216).
 *
 * Portalling the panel to `document.body` (ARCH-204) severed tab order from
 * visual order: Tab from an open menu walked into the page behind it and the
 * options were unreachable from the keyboard. The fix lives in the primitive,
 * so every consumer inherits it — and so a regression here would break nine
 * dropdowns at once, which is exactly what these cases are here to catch.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AnchoredMenu } from "./AnchoredMenu";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/** A trigger plus the menu that hangs off it, exactly as consumers wire it. */
function Harness({ open, children }: { open: boolean; children?: React.ReactNode }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  return (
    <>
      <button ref={setAnchor} id="trigger">
        Open
      </button>
      <button id="next">Next control on the page</button>
      <AnchoredMenu anchor={anchor} open={open} onClose={() => {}} role="listbox">
        {children ?? (
          <button id="option" role="option" aria-selected={false}>
            Option
          </button>
        )}
      </AnchoredMenu>
    </>
  );
}

let container: HTMLDivElement;
let root: Root;

function render(open: boolean, children?: React.ReactNode) {
  act(() => {
    root.render(<Harness open={open}>{children}</Harness>);
  });
}

const panel = () => document.querySelector<HTMLElement>('[role="listbox"]');
const el = (id: string) => document.querySelector<HTMLElement>(`#${id}`)!;

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

describe("AnchoredMenu focus management", () => {
  it("moves focus into the panel on open, so Tab walks the options", () => {
    render(false);
    expect(panel()).toBeNull();

    render(true);
    const menu = panel()!;
    expect(document.activeElement).toBe(menu);
    // -1 keeps the panel out of the tab order itself while still letting Tab
    // step from it into the options it contains.
    expect(menu.tabIndex).toBe(-1);
    expect(menu.contains(el("option"))).toBe(true);
  });

  it("returns focus to the trigger on close", () => {
    render(true);
    expect(document.activeElement).toBe(panel());

    render(false);
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(el("trigger"));
  });

  it("leaves a panel's own autofocused input alone", () => {
    render(
      true,
      <input id="search" autoFocus placeholder="Filter…" />,
    );
    // PairSelector and the venue filter type into this input the instant they
    // open; pulling focus back to the wrapper would eat the first keystroke.
    expect(document.activeElement).toBe(el("search"));
  });

  it("does not yank focus back from a control the user clicked outside", () => {
    render(true);
    el("next").focus();

    render(false);
    expect(document.activeElement).toBe(el("next"));
  });
});
