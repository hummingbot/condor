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
}

export interface ChartPriceMapping {
  startPrice: number;
  endPrice: number;
  limitPrice: number;
  side: 1 | 2;
  minSpread: number;
  activePickField: "start" | "end" | "limit" | null;
  extraLines?: ExtraLine[];
}

export interface ExecutorValidation {
  valid: boolean;
  errors: string[];
}
