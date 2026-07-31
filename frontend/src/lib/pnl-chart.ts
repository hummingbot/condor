// ── Shared helpers for PNL evolution charts ──

/**
 * Fixed series colors shared by strokes, axis ticks, header stats and tooltips
 * across AggregatedPnlChart and ControllerPnlChart. Realized and total are
 * theme-driven (getThemeColors / pnlColor), so only the three fixed series live here.
 */
export const PNL_SERIES_COLORS = {
  unrealized: "#f59e0b",
  volume: "#3b82f6",
  position: "#a78bfa",
} as const;

/** A single point on a PNL evolution chart (per-controller or aggregated). */
export interface PnlChartPoint {
  time: number;
  realized: number;
  unrealized: number;
  total: number;
  volume: number;
  position: number;
}

/** Compute net position value in quote from positions_summary */
export function positionQuoteValue(positions: Record<string, unknown>[]): number {
  let value = 0;
  for (const pos of positions) {
    const amt = Number(pos.amount || pos.net_amount_base || 0);
    const price = Number(pos.breakeven_price || pos.entry_price || pos.current_price || 0);
    const side = String(pos.side || pos.position_side || "");
    const isSell = side.toLowerCase().includes("sell") || side.toLowerCase().includes("short");
    const notional = amt * price;
    value += isSell ? -notional : notional;
  }
  return value;
}
