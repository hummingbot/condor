/**
 * A layout change is more than the pane: whatever else it moves waits on the
 * guard. `then` runs after the pane changes — at once, or after Discard — and
 * never on Cancel, so Cancel leaves the whole screen as it was.
 *
 * @vitest-environment jsdom
 */

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PaneView } from "./paneUrl";
import { usePaneGuard } from "./usePaneGuard";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const then = vi.fn();

function Host({ dirty }: { dirty: boolean }) {
  const [pane, setPane] = useState<PaneView>({ kind: "agent" });
  const guard = usePaneGuard({ pane, panelSlug: "orca", apply: setPane });
  return (
    <>
      <button data-dirty onClick={() => guard.onPanelDirtyChange(dirty)} />
      <button data-close onClick={() => guard.openPane(null, then)} />
      <button
        data-desk
        onClick={() =>
          // What the desk's own showOnly does inside an accepted pick: it asks
          // for the pane again, in the same breath.
          guard.openPane({ kind: "desk" }, () => {
            guard.openPane({ kind: "desk" });
            then();
          })
        }
      />
      <span data-pane>{pane?.kind ?? "none"}</span>
      {guard.dialog}
    </>
  );
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  then.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function click(el: Element) {
  await act(async () => {
    (el as HTMLElement).click();
  });
}

const q = (sel: string) => document.querySelector(sel)!;
const buttonNamed = (label: string) =>
  [...document.querySelectorAll<HTMLButtonElement>("button")].find(
    (b) => b.textContent?.trim() === label,
  )!;

async function arrive(dirty: boolean) {
  await act(async () => root.render(<Host dirty={dirty} />));
  await click(q("[data-dirty]"));
  await click(q("[data-close]"));
}

describe("openPane's then", () => {
  it("runs at once when nothing is unsaved", async () => {
    await arrive(false);
    expect(q("[data-pane]").textContent).toBe("none");
    expect(then).toHaveBeenCalledOnce();
  });

  it("waits for Discard", async () => {
    await arrive(true);
    expect(then).not.toHaveBeenCalled();
    await click(buttonNamed("Discard"));
    expect(q("[data-pane]").textContent).toBe("none");
    expect(then).toHaveBeenCalledOnce();
  });

  it("never runs on Cancel", async () => {
    await arrive(true);
    await click(buttonNamed("Cancel"));
    expect(q("[data-pane]").textContent).toBe("agent");
    expect(then).not.toHaveBeenCalled();
  });
});

describe("a pane move made inside an accepted change", () => {
  it("is not asked about a second time", async () => {
    await act(async () => root.render(<Host dirty />));
    await click(q("[data-dirty]"));
    await click(q("[data-desk]"));
    await click(buttonNamed("Discard"));
    expect(document.body.textContent).not.toContain("Discard changes?");
    expect(q("[data-pane]").textContent).toBe("desk");
    expect(then).toHaveBeenCalledOnce();
  });
});
