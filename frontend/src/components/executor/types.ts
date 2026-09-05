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

/** The three price lines the chart draws on its own, from `ChartPriceMapping`. */
export type ChartLineSlot = "start" | "end" | "limit";

/**
 * A price the chart can hand back when the user clicks or drags it.
 *
 * The three chart-owned lines, or an id a panel mints for one of its own extra
 * lines. The chart never interprets an id — it carries it from the `ExtraLine`
 * that declared it straight back into `onPriceSet` — so a panel names the slot
 * after the field behind it (`take_profit`, `dca_price_2`) and its write-back
 * reads as a dispatch, not a lookup through a translation table.
 *
 * The open half is what makes a panel with a variable number of prices work at
 * all: DCA has one line per level, and no fixed union can have a member per
 * level. `string & {}` keeps the three literals in autocomplete.
 */
export type PickSlot = ChartLineSlot | (string & {});

const CHART_LINE_SLOTS: readonly string[] = ["start", "end", "limit"];

/** Whether a slot is one of the chart's own three, rather than a panel's. */
export function isChartLineSlot(slot: PickSlot): slot is ChartLineSlot {
  return CHART_LINE_SLOTS.includes(slot);
}

export interface ChartPriceMapping {
  startPrice: number;
  endPrice: number;
  limitPrice: number;
  side: 1 | 2;
  minSpread: number;
  activePickField: PickSlot | null;
  extraLines?: ExtraLine[];
  /**
   * What the chart calls each slot's line and pick hint. The slot names are
   * internal plumbing ("start", "end", "limit"); every panel says here what its
   * prices actually are, so the two range executors share one vocabulary —
   * `Upper` / `Lower` for the bounds a grid or an LP works inside, `Upper limit`
   * / `Lower limit` for the stop that trails them — and the single-price panels
   * name their own price ("Entry", "Price", "Level").
   */
  lineLabels?: Partial<Record<PickSlot, string>>;
}

export interface ExecutorValidation {
  valid: boolean;
  errors: string[];
}
