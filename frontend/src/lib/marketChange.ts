/**
 * How the market browser talks about a price change it did not choose the
 * window for.
 *
 * The backend cannot ask the exchange for "24h change" — there is no such field
 * on the CLOB path — so it measures against its own hourly price snapshots and
 * ships the *actual* distance to the reference (`change_window_s`) with every
 * row. A number whose window the reader cannot see is worse than no number, so
 * the header is derived from that distance rather than hard-coded to 24h: 24h
 * only when it really is, the true hours otherwise, and a bare Δ when there is
 * nothing to compare against.
 */
const HOUR_S = 3600;

/** How close to 24h still reads as 24h. Snapshots are hourly, so ±30min. */
const TOLERANCE_H = 0.5;

export function changeColumnLabel(windowS: number | null): string {
  if (windowS == null || !Number.isFinite(windowS) || windowS <= 0) return "Δ";
  const hours = windowS / HOUR_S;
  if (Math.abs(hours - 24) <= TOLERANCE_H) return "24h Δ";
  if (hours >= 1) return `${Math.round(hours)}h Δ`;
  return `${Math.max(1, Math.round(windowS / 60))}m Δ`;
}

/** The exact age, for the header's and each cell's tooltip. */
export function changeWindowTitle(windowS: number | null): string {
  if (windowS == null || !Number.isFinite(windowS) || windowS <= 0) {
    return "No price history yet — the change column fills in once Condor has a reference snapshot.";
  }
  const hours = Math.floor(windowS / HOUR_S);
  const minutes = Math.round((windowS % HOUR_S) / 60);
  const age = hours ? `${hours}h ${minutes}m` : `${minutes}m`;
  return `Change measured over the last ${age}`;
}

/** Signed percent. Unlike `formatPct`, a real 0.00% is not a missing value. */
export function formatChange(pct: number | null): string {
  if (pct == null || !Number.isFinite(pct)) return "—";
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}
