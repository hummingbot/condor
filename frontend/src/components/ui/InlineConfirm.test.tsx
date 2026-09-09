/**
 * The affordance the six former hand-rolled copies now share (READ-352).
 *
 * The copies had drifted apart — a pending spinner in one, "Yes"/"No" in
 * another, an accessible name on some buttons and not others — so the point of
 * folding them into one primitive is that these guarantees hold everywhere at
 * once. A regression here is a regression in all six delete controls.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InlineConfirm } from "./InlineConfirm";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

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

const click = (el: Element | null) =>
  act(() => {
    el?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });

const byLabel = (name: string) =>
  container.querySelector(`[aria-label="${name}"]`) as HTMLButtonElement | null;

describe("InlineConfirm", () => {
  it("takes two clicks: the trigger asks, the confirm acts", () => {
    const onRequest = vi.fn();
    const onConfirm = vi.fn();

    act(() => {
      root.render(
        <InlineConfirm
          confirming={false}
          onRequest={onRequest}
          onConfirm={onConfirm}
          onCancel={vi.fn()}
          triggerLabel="Delete server"
        />,
      );
    });

    // Closed: only the trigger, and it never deletes on its own.
    expect(byLabel("Delete server")).not.toBeNull();
    expect(byLabel("Confirm delete")).toBeNull();
    click(byLabel("Delete server"));
    expect(onRequest).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();

    // The caller owns the state, so confirming arrives as a prop.
    act(() => {
      root.render(
        <InlineConfirm
          confirming
          onRequest={onRequest}
          onConfirm={onConfirm}
          onCancel={vi.fn()}
          triggerLabel="Delete server"
        />,
      );
    });

    click(byLabel("Confirm delete"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("gives the confirm and cancel buttons accessible names", () => {
    act(() => {
      root.render(
        <InlineConfirm
          confirming
          onRequest={vi.fn()}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
          triggerLabel="Remove wallet"
          confirmLabel="Confirm remove"
          cancelLabel="Cancel remove"
        />,
      );
    });

    expect(byLabel("Confirm remove")).not.toBeNull();
    expect(byLabel("Cancel remove")).not.toBeNull();
  });

  it("blocks a second submission while the mutation is pending", () => {
    const onConfirm = vi.fn();

    act(() => {
      root.render(
        <InlineConfirm
          confirming
          pending
          onRequest={vi.fn()}
          onConfirm={onConfirm}
          onCancel={vi.fn()}
          triggerLabel="Delete report"
        />,
      );
    });

    const confirm = byLabel("Confirm delete");
    expect(confirm?.disabled).toBe(true);
    expect(confirm?.querySelector(".animate-spin")).not.toBeNull();
  });

  it("refuses the first click when the trigger is disabled", () => {
    const onRequest = vi.fn();

    act(() => {
      root.render(
        <InlineConfirm
          confirming={false}
          disabled
          onRequest={onRequest}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
          triggerLabel="Owner access required"
        />,
      );
    });

    expect(byLabel("Owner access required")?.disabled).toBe(true);
  });
});
