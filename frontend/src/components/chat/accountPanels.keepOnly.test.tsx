/**
 * The layout picker moves the pane itself, once the unsaved-edits guard says
 * yes; `keepOnly` and `forget` then pick the desk's sections without asking
 * for the pane a second time — which pushed a duplicate history entry, so Back
 * had to be pressed twice to leave the desk.
 *
 * @vitest-environment jsdom
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAccountPanels } from "./accountPanels";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const onOpenChange = vi.fn();
const holder: { current: ReturnType<typeof useAccountPanels> | null } = { current: null };

function Host() {
  const panels = useAccountPanels({ server: "s", open: true, onOpenChange });
  useEffect(() => {
    holder.current = panels;
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  onOpenChange.mockReset();
  act(() => root.render(<Host />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("picking the desk's sections from the layout picker", () => {
  it("keepOnly shows one section without asking for the pane", () => {
    act(() => holder.current!.keepOnly("portfolio"));
    expect(holder.current!.shown).toEqual(["portfolio"]);
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("forget clears the sections without giving the pane back", () => {
    act(() => holder.current!.keepOnly("portfolio"));
    act(() => holder.current!.forget());
    expect(holder.current!.shown).toEqual([]);
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("showOnly still asks for the pane, for every other caller", () => {
    act(() => holder.current!.showOnly("portfolio"));
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });
});
