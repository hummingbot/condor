// ── The identity of one controller ──

/**
 * The identity fields every controller-shaped record carries.
 *
 * A live `ControllerInfo` off the `bots` payload and a stored
 * `ControllerPerformanceSnapshot` off the performance history are two views of
 * the same thing, so both are keyed through the one helper below.
 */
export interface ControllerIdentity {
  bot_name?: string;
  controller_id?: string;
  controller_name?: string;
}

/**
 * The key that identifies one controller: its bot joined to its controller id.
 *
 * `controller_id` on its own is **not** unique. It is the controller *config*
 * id (`condor/fetchers/bots.py`: `display_id = config_id or ctrl_name`), so
 * deploying one config to two bots yields two independent live controllers that
 * share it. Everything that keyed on the bare id silently mixed the two
 * together, and all four ways it went wrong were invisible (CORR-241):
 *
 *  - the fleet chart's snapshot filter kept a single deploy time per id,
 *    last-write-wins, so a bot deployed an hour ago truncated a five-day
 *    sibling's history to one hour;
 *  - the fold merged both bots' rows into one forward-filled series (their
 *    values alternating rather than summing) while the live "now" point
 *    iterated the controllers and *did* sum them — a step at the right edge;
 *  - both bots' rows shared one sparkline, so the table drew the same
 *    interleaved line twice;
 *  - the socket deduped frames on `id:timestamp`, discarding the second bot's
 *    snapshot at a shared dump timestamp as a repeat.
 *
 * So the composite is the identity, and it has to be the identity *everywhere*
 * controllers or snapshots are keyed, deduped, filtered or grouped — a single
 * site left on the bare id puts the bug straight back, in a form nothing
 * throws on. Returns `""` for a record with no controller id at all, which
 * every caller already treats as "drop this".
 */
export function controllerKey(c: ControllerIdentity): string {
  const cid = c.controller_id || c.controller_name || "";
  return cid ? `${c.bot_name || ""}:${cid}` : "";
}
