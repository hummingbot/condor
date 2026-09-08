// ── Grid executor config hook ──
//
// The fifth of the five. Order, Position, DCA and LP each hand the page one
// object — `{ state, dispatch, validation, chartProps, buildPayload, save,
// handleChartPriceSet }` — and the grid used to hand it nothing: the page held
// the reducer itself and wrote out, by hand, the validation it read, the chart
// mapping it drew, the clamp it applied on a chart click, the `grid_executor`
// payload it posted and the defaults it saved. Five switches over
// `ExecutorType` therefore had four one-line arms and one long one, and every
// executor-wide concern had to be implemented twice.
//
// The state machine itself stays in `lib/gridExecutor`, which `GridConfigPanel`
// and the DEX pool page also read; this is only the page-facing shape of it.

import { useMemo, useReducer } from "react";

import type { ChartPriceMapping, ExecutorValidation, PickSlot } from "./types";
import { isChartLineSlot } from "./types";
import {
  clampGridPrice,
  gridConfigErrors,
  gridLineLabels,
  gridReducer,
  loadGridDefaults,
  saveGridDefaults,
} from "@/lib/gridExecutor";
import type { GridState } from "@/lib/gridExecutor";

// ── Validation ──

export function useGridValidation(state: GridState): ExecutorValidation {
  return useMemo(() => {
    const errors = gridConfigErrors(state);
    return { valid: errors.length === 0, errors };
  }, [state]);
}

// ── Hook ──

export function useGridConfig() {
  // `applyLastMarket`: the grid's connector/pair are the page's market, shared
  // by every tab, so they come back from the last market rather than from the
  // grid's own saved defaults.
  const [state, dispatch] = useReducer(gridReducer, undefined, () => loadGridDefaults(true));
  const validation = useGridValidation(state);

  const chartProps: ChartPriceMapping = useMemo(() => ({
    startPrice: state.start_price,
    endPrice: state.end_price,
    limitPrice: state.limit_price,
    side: state.side,
    minSpread: state.min_spread_between_orders,
    activePickField: state.activePickField,
    lineLabels: gridLineLabels(state.side),
  }), [
    state.start_price,
    state.end_price,
    state.limit_price,
    state.side,
    state.min_spread_between_orders,
    state.activePickField,
  ]);

  const buildPayload = (connector: string, pair: string, isSpot: boolean) => {
    const config: Record<string, unknown> = {
      connector_name: connector,
      trading_pair: pair,
      side: state.side,
      start_price: state.start_price,
      end_price: state.end_price,
      limit_price: state.limit_price,
      total_amount_quote: state.total_amount_quote,
      min_order_amount_quote: state.min_order_amount_quote,
      min_spread_between_orders: state.min_spread_between_orders,
      max_open_orders: state.max_open_orders,
      max_orders_per_batch: state.max_orders_per_batch,
      order_frequency: state.order_frequency,
      leverage: isSpot ? 1 : state.leverage,
      activation_bounds: state.activation_bounds,
      keep_position: state.keep_position,
      coerce_tp_to_step: state.coerce_tp_to_step,
      triple_barrier_config: {
        take_profit: state.take_profit,
        open_order_type: state.open_order_type,
        take_profit_order_type: state.take_profit_order_type,
      },
    };

    return { executor_type: "grid_executor" as const, config };
  };

  const save = () => saveGridDefaults(state);

  /**
   * Write a price the user picked off the chart into the field behind `field`.
   *
   * `pricePrecision` is the one thing this hook cannot hold itself: it is read
   * off the venue's trading rules, which are queried for the connector and pair
   * that live in *this* state — so the page computes it downstream of the hook
   * and hands it back here. The other four panels clamp against nothing and
   * take two arguments.
   */
  const handleChartPriceSet = (
    field: PickSlot,
    price: number,
    pricePrecision?: number | null,
  ) => {
    // The grid owns exactly the chart's own three lines; any other slot belongs
    // to a panel that draws its own and would name a grid field that does not
    // exist.
    if (!isChartLineSlot(field)) return;
    // Bound the picked price against the two the user already set, so a click
    // (and, later, a drag) cannot write a price the form will only reject
    // afterwards. The chart stays ignorant of grid semantics.
    dispatch({
      type: "SET_FIELD",
      field: `${field}_price`,
      value: clampGridPrice(field, price, state, pricePrecision),
    });
    dispatch({ type: "SET_FIELD", field: "activePickField", value: null });
  };

  return { state, dispatch, validation, chartProps, buildPayload, save, handleChartPriceSet };
}
