/**
 * A conversation's token total, folded the way the backend folds it (FEAT-120).
 *
 * The dashboard seeds a slot from the conversation's stored total and then adds
 * each turn's `prompt_done.usage` to it, so these rules have to be the Python
 * `TokenUsage.__add__` rules exactly or a live tab would drift from a reloaded
 * one: counters sum, the context reading is the latest one that was reported.
 */

import type { TokenUsage } from "@/lib/api";

const COUNTERS = [
  "input_tokens",
  "output_tokens",
  "cache_read_tokens",
  "cache_write_tokens",
  "cost_usd",
  "unpriced_turns",
] as const;

/** Past this share of the window the context reading turns amber: the moment
 *  `/compact` becomes worth knowing about. */
export const CONTEXT_WARN_RATIO = 0.8;

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const reading = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/**
 * `total` plus one turn. Either side may be partial wire data — a meta written
 * before FEAT-120 carries `{}` — and a missing side changes nothing.
 */
export function addUsage(
  total: Partial<TokenUsage> | null | undefined,
  turn: Partial<TokenUsage> | null | undefined,
): TokenUsage | undefined {
  if (!total && !turn) return undefined;
  const a = total ?? {};
  const b = turn ?? {};
  const next = {} as TokenUsage;
  for (const key of COUNTERS) next[key] = num(a[key]) + num(b[key]);
  next.context_used = reading(b.context_used) ?? reading(a.context_used);
  next.context_size = reading(b.context_size) ?? reading(a.context_size);
  next.total_tokens = next.input_tokens + next.output_tokens;
  return next;
}

/**
 * How the cost figure shows, if at all.
 *
 * - `null` — a chat whose every priced figure is zero *because* nothing could be
 *   priced (a local model). "$0.00" there would be a claim, not a reading.
 * - `lowerBound` — some turns were priced and some were not (a chat that moved
 *   from Claude to a local model), so the figure is at least this much.
 */
export function costDisplay(u: TokenUsage): { lowerBound: boolean } | null {
  if (u.cost_usd <= 0 && u.unpriced_turns > 0) return null;
  return { lowerBound: u.cost_usd > 0 && u.unpriced_turns > 0 };
}

/** `38.2k`, `1.2M` — a token count at a glance. */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${trim(n / 1_000_000)}M`;
  if (n >= 1_000) return `${trim(n / 1_000)}k`;
  return String(Math.round(n));
}

function trim(v: number): string {
  // One decimal below 100, none above: `38.2k`, `200k`, never `200.0k`.
  return v >= 100 ? String(Math.round(v)) : v.toFixed(1).replace(/\.0$/, "");
}
