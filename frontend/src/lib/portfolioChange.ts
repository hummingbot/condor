import type { PortfolioHistoryPoint } from "@/lib/api";

/**
 * What the portfolio did over a window, split the way a broker splits it.
 *
 * `pnl` is the market move — what prices did to the holdings — and leaves
 * deposits and withdrawals out: putting $200 in is not a $200 gain. `flows` is
 * the rest of the change in value: money moved in (positive) or out.
 *
 * The server sends the market move as `pnl_usd` on each point. A server that
 * predates it sends none, and then the whole change is all that can be said,
 * so it is reported as P&L the way it always was.
 *
 * `null` with fewer than two points: one point is a level, not a change.
 */
export function portfolioChange(points: PortfolioHistoryPoint[]): {
  pnl: number;
  flows: number;
  pct: number | null;
} | null {
  if (points.length < 2) return null;
  const first = points[0];
  const last = points[points.length - 1];
  const total = last.total_usd - first.total_usd;
  const hasPnl = first.pnl_usd !== undefined && last.pnl_usd !== undefined;
  const pnl = hasPnl ? last.pnl_usd! - first.pnl_usd! : total;
  return {
    pnl,
    flows: total - pnl,
    pct: first.total_usd > 0 ? (pnl / first.total_usd) * 100 : null,
  };
}
