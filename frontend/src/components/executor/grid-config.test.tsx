/**
 * `useGridConfig().buildPayload` emits exactly what CreateExecutor used to build
 * by hand (ARCH-347).
 *
 * The grid was the one executor whose `grid_executor` payload was written out in
 * the page's mutation rather than by a `buildPayload`, so moving it into the hook
 * is a refactor with a wire format on the other side of it: these cases pin the
 * key set, the key *order*, the nested `triple_barrier_config`, and the spot
 * override that forces `leverage: 1`.
 *
 * Needs a DOM to run a hook, so this file overrides vitest's default `node`
 * environment.
 *
 * @vitest-environment jsdom
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useGridConfig } from "./grid-config";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

type GridConfig = ReturnType<typeof useGridConfig>;

let container: HTMLDivElement;
let root: Root;
let latest: GridConfig;

function Probe() {
  const config = useGridConfig();
  // Published from an effect rather than assigned during render: the render of
  // a component is not the place for a side effect, and `act` flushes effects
  // before it returns either way.
  useEffect(() => {
    latest = config;
  });
  return null;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(<Probe />);
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
});

/** Apply one field edit through the reducer and settle the render. */
function setField(field: string, value: unknown) {
  act(() => {
    latest.dispatch({ type: "SET_FIELD", field, value });
  });
}

describe("useGridConfig().buildPayload", () => {
  it("emits the grid_executor config CreateExecutor used to build inline", () => {
    setField("side", 2);
    setField("start_price", 100);
    setField("end_price", 110);
    setField("limit_price", 99);
    setField("total_amount_quote", 500);
    setField("leverage", 7);

    const payload = latest.buildPayload("binance_perpetual", "SOL-USDC", false);
    const state = latest.state;

    expect(payload.executor_type).toBe("grid_executor");
    // Byte-equal, key order included — this is what goes on the wire.
    expect(JSON.stringify(payload.config)).toBe(
      JSON.stringify({
        connector_name: "binance_perpetual",
        trading_pair: "SOL-USDC",
        side: 2,
        start_price: 100,
        end_price: 110,
        limit_price: 99,
        total_amount_quote: 500,
        min_order_amount_quote: state.min_order_amount_quote,
        min_spread_between_orders: state.min_spread_between_orders,
        max_open_orders: state.max_open_orders,
        max_orders_per_batch: state.max_orders_per_batch,
        order_frequency: state.order_frequency,
        leverage: 7,
        activation_bounds: state.activation_bounds,
        keep_position: state.keep_position,
        coerce_tp_to_step: state.coerce_tp_to_step,
        triple_barrier_config: {
          take_profit: state.take_profit,
          open_order_type: state.open_order_type,
          take_profit_order_type: state.take_profit_order_type,
        },
      }),
    );
  });

  it("forces leverage to 1 on a spot venue without touching the form's own value", () => {
    setField("leverage", 7);

    const payload = latest.buildPayload("binance", "SOL-USDC", true);

    expect(payload.config.leverage).toBe(1);
    // The state keeps what the user typed, so switching back to a perp venue
    // does not silently lose their leverage.
    expect(latest.state.leverage).toBe(7);
  });
});

describe("useGridConfig().handleChartPriceSet", () => {
  it("writes the picked price into the slot's field and disarms the picker", () => {
    setField("activePickField", "start_price");

    act(() => {
      latest.handleChartPriceSet("start", 123.45);
    });

    expect(latest.state.start_price).toBe(123.45);
    expect(latest.state.activePickField).toBeNull();
  });

  it("clamps the picked price against the prices already set", () => {
    setField("end_price", 110);

    act(() => {
      latest.handleChartPriceSet("start", 120);
    });

    // A lower bound above the upper one is not a range; the clamp lands it just
    // below rather than letting the form go red under the click.
    expect(latest.state.start_price).toBeLessThan(110);
  });

  it("ignores a slot the grid does not own", () => {
    setField("activePickField", "start_price");

    act(() => {
      latest.handleChartPriceSet("take_profit", 500);
    });

    // Not the grid's line: nothing written, and the grid's own armed picker is
    // left alone for the panel that does own the slot.
    expect(latest.state.start_price).toBe(0);
    expect(latest.state.activePickField).toBe("start_price");
  });
});
