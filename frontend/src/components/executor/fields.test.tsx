/**
 * `PriceField`'s crosshair is unconditional (READ-235).
 *
 * The button used to sit behind a `pickable` guard whose docstring described a
 * per-panel pick budget — "a panel that has run out of pick slots turns the
 * crosshair off". No panel ever implemented that; none of the seven call sites
 * passed the prop, and `PickSlot` is open precisely so a panel can mint a slot
 * per price and offer a crosshair for every one. The prop is gone, and
 * these cases pin the behaviour it was silently defaulting to, so nobody
 * reintroduces a conditional crosshair without a panel that actually needs one.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { NumberField, PriceField, type FieldDispatch } from "./fields";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let dispatched: Parameters<FieldDispatch>[0][];

function render(props: Partial<React.ComponentProps<typeof PriceField>> = {}) {
  act(() => {
    root.render(
      <PriceField
        label="Upper Price"
        value={0}
        field="upper_price"
        activePickField={null}
        dispatch={(action) => dispatched.push(action)}
        valid
        {...props}
      />,
    );
  });
}

const crosshair = () => container.querySelector<HTMLButtonElement>('button[title="Pick from chart"]');

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  dispatched = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("PriceField crosshair", () => {
  it("renders for every price, whatever the field or its state", () => {
    // The four LP prices are the panel the retired budget rule would have bitten
    // first: four prices, four slots, four crosshairs.
    for (const field of ["upper_price", "lower_price", "upper_limit_price", "lower_limit_price"]) {
      render({ field, value: 100, valid: false });
      expect(crosshair(), `no crosshair for ${field}`).not.toBeNull();
    }
  });

  it("claims the pick slot on click and hands it back on a second click", () => {
    render();
    act(() => crosshair()!.click());
    expect(dispatched).toEqual([
      { type: "SET_FIELD", field: "activePickField", value: "upper_price" },
    ]);

    // Once the chart reports this field as active, the same button releases it.
    render({ activePickField: "upper_price" });
    act(() => crosshair()!.click());
    expect(dispatched[1]).toEqual({ type: "SET_FIELD", field: "activePickField", value: null });
  });
});

describe("NumberField percentages", () => {
  /** Render one percent field and read back what the input shows. */
  function shown(value: number): string {
    act(() => {
      root.render(
        <NumberField
          label="Take Profit"
          value={value}
          field="take_profit"
          dispatch={(action) => dispatched.push(action)}
          isPercent
        />,
      );
    });
    return container.querySelector("input")!.value;
  }

  it("shows a scaled fraction without its float noise", () => {
    // Dragging a barrier line rewrites this field on every frame, so a value
    // that renders as 2.1199999999999997 is a value the user watches flicker.
    expect(shown(0.0212)).toBe("2.12");
    expect(shown(0.0007)).toBe("0.07");
    expect(shown(0.0035)).toBe("0.35");
  });

  it("still shows an empty field for a barrier that is switched off", () => {
    expect(shown(0)).toBe("");
  });
});
