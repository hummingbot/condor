import type { PortfolioHistoryPoint } from "@/lib/api";

/**
 * P&L over a window of portfolio history, deposits and withdrawals left out
 * (#350): the server's market P&L (`pnl_usd`) when it sends one, otherwise the
 * change in value as before. `null` with fewer than two points.
 */
export function portfolioPnl(points: PortfolioHistoryPoint[]): number | null {
  if (points.length < 2) return null;
  const first = points[0];
  const last = points[points.length - 1];
  if (first.pnl_usd !== undefined && last.pnl_usd !== undefined) {
    return last.pnl_usd - first.pnl_usd;
  }
  return last.total_usd - first.total_usd;
}
