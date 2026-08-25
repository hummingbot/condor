/**
 * The one copy of the unknown-input number/USD/percent formatters and the pool
 * pair-label rule the DEX components share. PoolBrowser, PoolStats and
 * lp-position each grew a private copy of these that silently diverged
 * (zero-vs-missing semantics, percent capping, a half-guarded "???"); this
 * module is the single definition they all import.
 */

import type { PoolSummary } from "@/lib/api";

/**
 * Both upstreams answer numbers as strings (and GeckoTerminal answers pandas NaN
 * for a missing figure). The backend settles that before it serializes, so this
 * is the second line of defence rather than the first — but it is the one that
 * decides whether a stray string blanks one cell or takes down the whole page.
 *
 * Null-preserving: a missing figure stays `null` so callers can tell "no data"
 * from a genuine zero.
 */
export function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : null;
}

/**
 * Compact dollars with a billions tier for pool TVL and volume figures.
 * Only a *missing* figure renders as an em-dash; a genuine `$0` renders as
 * `$0.00` — zero TVL is a fact about the pool, not absent data.
 */
export function usdCompact(v: unknown): string {
  const n = num(v);
  if (n === null) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
}

/**
 * Percent with a K% cap: a 25000% memecoin move renders as "25K%" instead of
 * stretching the column with "25000.00%". Em-dash for a missing figure.
 */
export function pct(v: unknown): string {
  const n = num(v);
  if (n === null) return "—";
  if (Math.abs(n) >= 10_000) return `${(n / 1000).toFixed(0)}K%`;
  return `${n.toFixed(2)}%`;
}

/**
 * The two sides of a pool, each only when it is actually a name.
 *
 * GeckoTerminal answers `"???"` for a token it cannot name, and the backend keeps
 * the sides independent — `extract_pair_from_name` returns `(name, "???")` for a
 * pool name with no separator, and `decorate_pool` writes each symbol from its
 * own token dict. So a known base beside an unnamed quote is a real payload, and
 * every consumer has to guard both sides rather than one.
 *
 * Accepts `undefined` because the pool workspace reads the symbols above its own
 * early returns, while the pool is still loading.
 */
export function poolSymbols(p: PoolSummary | undefined): {
  base?: string;
  quote?: string;
} {
  const named = (s: string | undefined) => (s && s !== "???" ? s : undefined);
  return { base: named(p?.base_symbol), quote: named(p?.quote_symbol) };
}

/**
 * A pool's pair label: `BASE-QUOTE` when both sides are named, else the pool's
 * own name, else a truncated address.
 *
 * Never renders a `"???"`: a half-named pair is not a label, so it falls through
 * to the name the upstream gave the pool. The address is the last resort because
 * an empty header is worse than an unreadable one.
 */
export function poolLabel(p: PoolSummary): string {
  const { base, quote } = poolSymbols(p);
  return base && quote ? `${base}-${quote}` : p.name || p.address.slice(0, 8);
}
