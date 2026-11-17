/**
 * Escape closes the topmost overlay, and only that one (FU-05).
 *
 * Every `useEscapeKey` caller used to hang its own listener on `window`, so a
 * single keypress reached all of them: a discard dialog and the editor under
 * it closed together, and the chat's "Esc stops the answer" fired for an
 * Escape meant for a menu. The handlers are a stack now.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useEscapeKey, type EscapeKeyOptions } from "./useEscapeKey";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const fired: string[] = [];

function Layer({
  name,
  active = true,
  options,
}: {
  name: string;
  active?: boolean;
  options?: EscapeKeyOptions;
}) {
  useEscapeKey(active, () => fired.push(name), options);
  return null;
}

let container: HTMLDivElement;
let root: Root;

function render(node: React.ReactNode) {
  act(() => root.render(node));
}

/** One Escape; returns whether a handler claimed it. */
function escape(target: EventTarget = document.body): boolean {
  const event = new KeyboardEvent("keydown", {
    key: "Escape",
    bubbles: true,
    cancelable: true,
  });
  act(() => {
    target.dispatchEvent(event);
  });
  return event.defaultPrevented;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  fired.length = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("useEscapeKey", () => {
  it("fires only the handler opened last", () => {
    render(
      <>
        <Layer name="editor" />
        <Layer name="dialog" active={false} />
      </>,
    );
    render(
      <>
        <Layer name="editor" />
        <Layer name="dialog" />
      </>,
    );

    expect(escape()).toBe(true);
    expect(fired).toEqual(["dialog"]);
  });

  it("hands the key to the layer underneath once the top one closes", () => {
    render(
      <>
        <Layer name="editor" />
        <Layer name="dialog" active={false} />
      </>,
    );
    render(
      <>
        <Layer name="editor" />
        <Layer name="dialog" />
      </>,
    );
    render(
      <>
        <Layer name="editor" />
        <Layer name="dialog" active={false} />
      </>,
    );

    escape();
    expect(fired).toEqual(["editor"]);
  });

  it("keeps a base handler under an overlay that opened before it", () => {
    render(<Layer name="menu" />);
    render(
      <>
        <Layer name="menu" />
        <Layer name="abort" options={{ base: true }} />
      </>,
    );

    escape();
    expect(fired).toEqual(["menu"]);

    render(<Layer name="abort" options={{ base: true }} />);
    escape();
    expect(fired).toEqual(["menu", "abort"]);
  });

  it("falls through a handler whose `when` declines the keypress", () => {
    let take = false;
    render(
      <>
        <Layer name="page" />
        <Layer name="picky" active={false} />
      </>,
    );
    render(
      <>
        <Layer name="page" />
        <Layer name="picky" options={{ when: () => take }} />
      </>,
    );

    escape();
    expect(fired).toEqual(["page"]);

    take = true;
    escape();
    expect(fired).toEqual(["page", "picky"]);
  });

  it("claims nothing when no handler is active, and ignores other keys", () => {
    render(<Layer name="idle" active={false} />);
    expect(escape()).toBe(false);

    render(<Layer name="idle" />);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    });
    expect(fired).toEqual([]);
  });

  it("is gone after unmount", () => {
    render(<Layer name="gone" />);
    render(null);

    expect(escape()).toBe(false);
    expect(fired).toEqual([]);
  });
});
