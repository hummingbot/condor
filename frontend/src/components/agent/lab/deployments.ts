// ── What a run put into the world, as rules (FEAT-100) ──
//
// The ledger's decisions that are not decisions about pixels: what order the
// rows read in, which glyph names each kind, how the live column is worded, and
// where a row links into the fleet. Pure, no React and no fetching (the ARCH-300
// split), so each rule below is reachable from a test rather than only from a
// rendered table.
//
// The tick column is deliberately not a rule here — the backend already decided
// it, and it is allowed to say nothing. `formatTick` only writes down the one
// thing this side must not get wrong: an unknown tick is a dash, never a zero
// and never a guess.

import type { DeploymentRow } from "@/lib/api";

/**
 * Bots first, then the controllers those bots ran, then standalone executors.
 *
 * That is the order the world was made in — a deploy carries its controllers,
 * and an executor the loop created itself belongs to neither — and it is the
 * order a reader scans for "what is this run running". Within a kind the rows
 * keep their start order, oldest first, so a run reads down the page the way it
 * happened.
 */
const KIND_RANK: Record<DeploymentRow["kind"], number> = {
  bot: 0,
  controller: 1,
  executor: 2,
};

export function orderDeployments(rows: readonly DeploymentRow[]): DeploymentRow[] {
  return [...rows].sort((a, b) => {
    const rank = KIND_RANK[a.kind] - KIND_RANK[b.kind];
    if (rank !== 0) return rank;
    return a.started_at - b.started_at;
  });
}

/** The glyph that names a kind, matching the fleet's own vocabulary. */
export function kindIcon(kind: DeploymentRow["kind"]): string {
  if (kind === "bot") return "🤖";
  if (kind === "controller") return "⚙";
  return "⚡";
}

/**
 * How the live column reads.
 *
 * Never derived from a performance snapshot's `status`, which says "running"
 * for an archived instance too. A bot released mid-run reads *closed* even
 * while the instance it deployed keeps trading for whoever owns it now — that
 * is the whole point of recording `until`.
 */
export function liveLabel(row: Pick<DeploymentRow, "live">): string {
  return row.live ? "live" : "closed";
}

/** The tick that created a row, or a dash. A guess would be worse than blank. */
export function formatTick(tick: number | null): string {
  return tick === null || tick === undefined ? "—" : `tick ${tick}`;
}

/**
 * Where *see this in the fleet* goes.
 *
 * The `bot:` / `ctrl:` / `exec:` grammar is the scope tree's own, so a row links
 * to exactly the node that is that thing — not to the agent, which is what the
 * strategy page's one gesture did and which folds the strategy's whole history
 * instead of the run you were reading.
 */
export function fleetHref(row: Pick<DeploymentRow, "scope">): string | null {
  return row.scope ? `/bots?scope=${encodeURIComponent(row.scope)}` : null;
}
