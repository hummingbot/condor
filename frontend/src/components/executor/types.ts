export type ExecutorType = "grid" | "position" | "order" | "dca";

export const EXECUTOR_TYPES: { value: ExecutorType; label: string; icon: string }[] = [
  { value: "grid", label: "Grid", icon: "Grid3X3" },
  { value: "position", label: "Position", icon: "TrendingUp" },
  { value: "order", label: "Order", icon: "ArrowUpDown" },
  { value: "dca", label: "DCA", icon: "Layers" },
];

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
