// ── Where a triple barrier sits, and what dragging it means ──
//
// The position and DCA executors both express their exits as a percentage of an
// anchor price — the entry for a position, the break-even for a DCA ladder — so
// both drew the same two lines from the same two formulas, written out twice.
// Dragging those lines needs the inverse as well, which is the point at which
// one shared pair belongs here: the chart hands back a price, and the panel has
// to answer with the percentage that puts the line exactly there.

import type { PickSlot } from "./types";

/** The slot ids the two barrier lines carry, shared so the panels agree. */
export const TAKE_PROFIT_SLOT: PickSlot = "take_profit";
export const STOP_LOSS_SLOT: PickSlot = "stop_loss";

export type BarrierKind = "tp" | "sl";

/**
 * How close to the anchor a dragged barrier may land: 0.1%.
 *
 * Not a trading rule — a floor that keeps the gesture reversible. A barrier
 * dragged to exactly its anchor is 0%, which means *disabled*: the line would
 * vanish mid-drag and leave nothing to grab on the way back.
 */
export const MIN_BARRIER_PCT = 0.001;

/** The cap the panels' own validation already enforces on both barriers. */
export const MAX_BARRIER_PCT = 1;

/**
 * Which way a barrier sits from its anchor: a long takes profit above and stops
 * below, a short does both the other way round.
 */
function offsetSign(side: 1 | 2, kind: BarrierKind): 1 | -1 {
  const up = kind === "tp";
  return (side === 1) === up ? 1 : -1;
}

/**
 * The price a barrier of `pct` sits at, or `0` when there is nothing to draw —
 * no anchor yet, or the barrier switched off with a zero percentage.
 */
export function barrierPrice(anchor: number, pct: number, side: 1 | 2, kind: BarrierKind): number {
  if (!(anchor > 0) || !(pct > 0)) return 0;
  return anchor * (1 + offsetSign(side, kind) * pct);
}

/**
 * The percentage that puts a barrier at `price`, clamped to the range the
 * panels accept and rounded to a basis point — the precision their percent
 * inputs render, so the number the drag writes is one the field can show back.
 */
export function barrierPct(anchor: number, price: number, side: 1 | 2, kind: BarrierKind): number {
  if (!(anchor > 0) || !Number.isFinite(price)) return 0;
  const raw = offsetSign(side, kind) * (price / anchor - 1);
  const clamped = Math.min(MAX_BARRIER_PCT, Math.max(MIN_BARRIER_PCT, raw));
  return Number(clamped.toFixed(4));
}

/** The label a barrier line carries, percentage included. */
export function barrierLabel(kind: BarrierKind, pct: number): string {
  return `${kind === "tp" ? "TP" : "SL"} (${(pct * 100).toFixed(1)}%)`;
}
