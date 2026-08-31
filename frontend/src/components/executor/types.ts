/**
 * An executor type the trade panel implements a tab for.
 *
 * This is the single union: `connectorCapabilities` reports which of these a venue
 * supports, `TYPE_TABS` / `TYPE_LABELS` render them, and four switches in
 * `CreateExecutor` are exhaustive over it — so adding a member here is what makes
 * the compiler point at every site that has to learn about it.
 */
export type ExecutorType = "grid" | "position" | "order" | "dca" | "lp";

export interface ExtraLine {
  price: number;
  label: string;
  color: string;
  lineStyle: "solid" | "dashed" | "dotted";
  lineWidth?: number;
  /**
   * The pick slot this line stands for, when it stands for one.
   *
   * The chart draws three prices of its own and cannot tell which of a panel's
   * extra lines is a price the user may set — so a panel says so here, and the
   * line becomes grabbable like the chart's own. A purely decorative extra line
   * omits it and stays inert.
   */
  slot?: PickSlot;
}

/**
 * A price the chart can hand back when the user clicks it.
 *
 * `start` / `end` / `limit` are the three lines the chart draws itself; `limit2`
 * is a fourth slot for a price a panel draws as an extra line (the LP lower
 * limit), so a panel with four prices offers a crosshair for every one of them.
 */
export type PickSlot = "start" | "end" | "limit" | "limit2";

export interface ChartPriceMapping {
  startPrice: number;
  endPrice: number;
  limitPrice: number;
  side: 1 | 2;
  minSpread: number;
  activePickField: PickSlot | null;
  extraLines?: ExtraLine[];
  /**
   * What the chart calls each slot's line and pick hint. The slots are named
   * for the grid executor ("Start", "End", "Limit"); a panel that rides them
   * with different semantics — LP's upper/lower bounds — relabels them here
   * rather than letting the chart mislabel its prices.
   */
  lineLabels?: Partial<Record<PickSlot, string>>;
}

export interface ExecutorValidation {
  valid: boolean;
  errors: string[];
}
