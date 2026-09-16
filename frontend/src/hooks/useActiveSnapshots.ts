/**
 * The fleet chart's snapshot filter, held still between `controller_perf` frames.
 *
 * The set of performance snapshots the fleet browser draws is
 * `perfHistory.snapshots` narrowed to the controllers that are currently
 * deployed, each one cut at its own deploy time. Both halves of that verdict
 * come from the live `bots` payload — and that payload is re-broadcast whole
 * every ~5s (`hummingbot_ws.py` subscribes with `update_interval=5.0`, and
 * `shared-socket.ts` writes a fresh `controllers` array of fresh objects into
 * the cache for each frame). Live PnL cells move on every one of those frames,
 * so structural sharing can never hold the array still.
 *
 * Memoizing the filter on the `controllers` array therefore re-ran it roughly
 * every 5 seconds, walking the fleet's *entire* history — thousands to tens of
 * thousands of rows at the sampling intervals PERF-238 picks — with a
 * `controllerKey` and a `Date.parse` per row, and handing every downstream fold
 * a brand-new array to re-aggregate. All of that to answer the same question
 * with the same answer: `perfHistory` itself only changes on the 30s
 * `controller_perf` frame.
 *
 * So the identity the filter actually depends on is split out of the payload
 * (PERF-334). Membership and deploy time are everything the predicate reads;
 * nothing else about a controller can change its verdict. Keying the deploy map
 * on a roster signature — the controller keys joined to their deploy times —
 * means a frame that moved only PnL leaves the map, and therefore the filtered
 * array, referentially identical, and the walk happens only when the roster or
 * a deploy time genuinely changes.
 */
import { useMemo } from "react";

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";

/** Held still, so "no controllers yet" is not a new array every render. */
const EMPTY_SNAPSHOTS: ControllerPerformanceSnapshot[] = [];

/**
 * Everything about the roster that can change the filter's verdict, as a string.
 *
 * Which controllers exist and when each was deployed — nothing else. Cheap to
 * recompute every render (one pass over a fleet-sized list of controllers, not
 * over the history), and equal across frames that moved only PnL.
 */
export function deployRosterSignature(controllers: ControllerInfo[]): string {
  return controllers.map((c) => `${controllerKey(c)}:${c.deployed_at ?? ""}`).join("|");
}

/**
 * Active controller key → its deploy time in ms (0 when unknown).
 *
 * Keyed by bot + controller, because a bare controller id is a config id two
 * bots can share: one map entry per id meant last-write-wins on the deploy
 * time, so an hour-old bot truncated its five-day sibling's history to an hour
 * of points (CORR-241).
 */
export function buildDeployByKey(controllers: ControllerInfo[]): Map<string, number> {
  const deployByKey = new Map<string, number>();
  for (const ctrl of controllers) {
    const deployMs = ctrl.deployed_at ? Date.parse(ctrl.deployed_at) : 0;
    deployByKey.set(controllerKey(ctrl), deployMs);
  }
  return deployByKey;
}

/** The snapshots belonging to an active controller, from its deploy time on. */
export function filterActiveSnapshots(
  snapshots: ControllerPerformanceSnapshot[],
  deployByKey: Map<string, number>,
): ControllerPerformanceSnapshot[] {
  return snapshots.filter((snap) => {
    const key = controllerKey(snap);
    if (!key || !deployByKey.has(key)) return false;
    const deployMs = deployByKey.get(key)!;
    if (!deployMs) return true; // no deploy time known, keep it
    const snapMs = Date.parse(snap.timestamp) || 0;
    return snapMs >= deployMs;
  });
}

/**
 * The filtered snapshot set, recomputed only when it can actually differ.
 *
 * Stable across every `bots` frame that left the roster and its deploy times
 * alone, which is nearly all of them.
 */
export function useActiveSnapshots(
  snapshots: ControllerPerformanceSnapshot[] | undefined,
  controllers: ControllerInfo[],
): ControllerPerformanceSnapshot[] {
  const signature = deployRosterSignature(controllers);

  // Keyed on the signature, not the array: the array is fresh every frame and
  // the signature is not. `controllers` is read here only to rebuild the map
  // the signature already decided has changed.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const deployByKey = useMemo(() => buildDeployByKey(controllers), [signature]);

  return useMemo(() => {
    if (!snapshots || deployByKey.size === 0) return EMPTY_SNAPSHOTS;
    return filterActiveSnapshots(snapshots, deployByKey);
  }, [snapshots, deployByKey]);
}
