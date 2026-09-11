/**
 * The positions band's data layer: what an open position is, in table terms.
 *
 * Lifted out of `PerfBrowser` (ARCH-300) with its behaviour unchanged. Kept
 * apart from `components/perf/PositionsTable.tsx` because a module that
 * exports a React component may not also export plain helpers
 * (`react-refresh/only-export-components`), and because the row builder is
 * pure over a leaf set and a converter — it is the half worth testing.
 */

import type { PerfLeaf } from "@/lib/perf-tree";

/** The keys the table has its own column for; anything else is an extra. */
export const POSITION_PRIMARY_KEYS = new Set([
  "connector_name",
  "connector",
  "trading_pair",
  "side",
  "realized_pnl_quote",
  "unrealized_pnl_quote",
  "volume_traded_quote",
  "volume_traded",
  "amount",
  "breakeven_price",
]);

export interface PositionRow {
  id: string;
  ctrlLabel: string;
  pair: string;
  connector: string;
  side: string;
  amount: number | null;
  breakeven: number | null;
  /** Amount x breakeven, in display currency: what the inventory is worth.
      Unsigned — the Side column already says which way it points. */
  notional: number | null;
  realized: number;
  unrealized: number;
  volume: number;
  extras: [string, unknown][];
}

/** Converts a leaf-quoted figure into display currency, through its pair. */
export type QuoteConvert = (val: number, pair: string) => number;

export function num(v: unknown): number | null {
  const n = Number(v);
  return v === undefined || v === null || Number.isNaN(n) ? null : n;
}

/** Prices and amounts vary over many orders of magnitude; trim rather than pad. */
export function formatAmount(v: number | null): string {
  if (v === null) return "—";
  if (v === 0) return "0";
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 8;
  return v.toFixed(digits).replace(/\.?0+$/, "");
}

/**
 * The tail of a dotted enum: `TradeType.BUY` → `BUY`.
 *
 * Upstream sends sides and close types as the repr of a Python enum. Used for
 * both, which is why it sits beside the position helpers rather than inside
 * the table that drew it first.
 */
export function parseSide(raw: string): string {
  const dot = raw.lastIndexOf(".");
  return dot >= 0 ? raw.slice(dot + 1) : raw;
}

/**
 * The quote value of an open position, in display currency.
 *
 * `positions_summary` carries no mark price, so the breakeven is the price we
 * have — this is the cost basis of the inventory, not its mark-to-market
 * value; the two differ by exactly the Unrealized column beside it. Same
 * convention as `positionQuoteValue` in lib/pnl-chart, which is what the
 * chart's position series is built from, so the two agree.
 */
export function positionNotional(
  pos: Record<string, unknown>,
  pair: string,
  cv: QuoteConvert,
): number | null {
  const amount = num(pos.amount);
  const price = num(pos.breakeven_price);
  if (amount === null || price === null) return null;
  return cv(Math.abs(amount * price), pair);
}

/** Every open position across a scope's leaves, one row each, in display currency. */
export function buildPositionRows(leaves: PerfLeaf[], cv: QuoteConvert): PositionRow[] {
  const rows: PositionRow[] = [];
  for (const leaf of leaves) {
    leaf.positions.forEach((pos, i) => {
      const pair = String(pos.trading_pair || leaf.pair || "");
      rows.push({
        id: `${leaf.id}#${i}`,
        ctrlLabel: leaf.label,
        pair,
        connector: String(pos.connector_name || pos.connector || leaf.connector || ""),
        side: parseSide(String(pos.side || "")),
        amount: num(pos.amount),
        breakeven: num(pos.breakeven_price),
        notional: positionNotional(pos, pair, cv),
        realized: cv(Number(pos.realized_pnl_quote || 0), pair),
        unrealized: cv(Number(pos.unrealized_pnl_quote || 0), pair),
        volume: cv(Number(pos.volume_traded_quote || pos.volume_traded || 0), pair),
        extras: Object.entries(pos).filter(([k]) => !POSITION_PRIMARY_KEYS.has(k)),
      });
    });
  }
  return rows;
}

/** Extra per-position fields, as columns, so the table stays one grid. */
export function positionExtraColumns(rows: PositionRow[]): string[] {
  const seen: string[] = [];
  for (const row of rows) {
    for (const [k] of row.extras) if (!seen.includes(k)) seen.push(k);
  }
  return seen;
}
