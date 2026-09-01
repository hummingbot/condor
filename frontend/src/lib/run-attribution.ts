// ── Which run created a closed executor (FEAT-089) ──
//
// An `ExecutorInfo` carries a `controller_id` and no bot (see `condor/web/models.py`),
// so attributing one means answering "which bot was running that controller at
// that moment". The browser used to answer it by matching the id against the
// *live* fleet, which cannot be right for a population defined as what has
// finished: a controller of a stopped bot matches nothing, and a config id two
// live bots share matches ambiguously and was nulled.
//
// The answer is in the run history itself. A run's `deployment_config` names
// every controller it was deployed with (`BotRunInfo.controller_ids`), which is
// the deployment's own declaration — correct for a run with no performance
// snapshots left at all, and it makes a shared id resolvable by *when* rather
// than unresolvable. So the question becomes an interval lookup: which run
// window contains this instant, among the runs that declared this controller.
//
// **What this does not claim.** Measured against three real servers, every row
// in the API's executors table carries `controller_id: "main"` and no run
// declares a controller by that name — because `main` is Condor's own default
// for an executor opened by hand (`CreateExecutorRequest.controller_id`), and a
// bot's controller executors live inside its container and never reach that
// table at all. On those fleets this attributes nothing, which is the right
// answer: those positions genuinely belong to no run, and `(unattached)` is
// where they belong. What it buys is that the bucket is now *provably* that
// rather than accidentally that — and a deployment whose executors do carry
// their controller's id gets them filed under the run that opened them.

import type { BotRunInfo } from "@/lib/api";

/** One run's life, and the controllers it declared for that life. */
export interface RunWindow {
  bot: string;
  /** Epoch ms. A run with no deploy time has no window and is skipped. */
  deployedAt: number;
  /** Epoch ms, or null while the run is still live — an open-ended window. */
  stoppedAt: number | null;
  controllerIds: string[];
}

/**
 * Turn the wire's runs into windows, dropping the ones that cannot bound one.
 *
 * A run with no `created_at` has no start, and a window with no start contains
 * every instant — it would swallow every executor on the server. A run that
 * declared no controllers indexes nothing and is dropped for cost, not
 * correctness.
 */
export function runWindows(runs: readonly BotRunInfo[]): RunWindow[] {
  const windows: RunWindow[] = [];
  for (const run of runs) {
    if (!run.controller_ids?.length) continue;
    const deployedAt = run.created_at ? Date.parse(run.created_at) : NaN;
    if (Number.isNaN(deployedAt)) continue;
    const stopped = run.stopped_at ? Date.parse(run.stopped_at) : NaN;
    windows.push({
      bot: run.bot_name,
      deployedAt,
      // A live run is open-ended, and so is one whose stop time is unreadable:
      // ending its window at the parse failure would orphan everything it
      // traded after it.
      stoppedAt: run.is_live || Number.isNaN(stopped) ? null : stopped,
      controllerIds: run.controller_ids,
    });
  }
  return windows;
}

/** One controller's windows, ordered, with the reach of everything before each. */
interface ControllerIndex {
  windows: RunWindow[];
  /**
   * `maxEnd[i]` is the latest end among `windows[0..i]`, `Infinity` for an
   * open-ended one.
   *
   * This is what bounds the backward scan. Windows are ordered by *start*, so a
   * window containing `t` may sit arbitrarily far back behind shorter ones that
   * started later — a correct lookup has to consider every earlier window, and
   * a fleet that reuses one config id for a year has thousands. The running
   * maximum lets the scan stop the moment nothing earlier can still reach `t`,
   * which for non-overlapping runs — the normal case — is immediately.
   */
  maxEnd: number[];
}

/**
 * Which bot was running `controllerId` at `atMs`, or `null`.
 *
 * `null` is returned for two different situations, deliberately collapsed:
 * no window contains the instant (a position opened by hand, or a run older
 * than the history we hold), and two concurrent runs both claim it. The second
 * is a real ambiguity — crediting the trading to whichever run was seen first
 * would put one bot's PnL under another's name — and both answers lead to the
 * same place on screen, which is the bucket for executors that belong to no
 * one run.
 */
export type Attributor = (controllerId: string, atMs: number) => string | null;

export function buildAttributor(runs: readonly RunWindow[]): Attributor {
  const index = new Map<string, ControllerIndex>();

  for (const window of runs) {
    for (const id of window.controllerIds) {
      if (!id) continue;
      let entry = index.get(id);
      if (!entry) {
        entry = { windows: [], maxEnd: [] };
        index.set(id, entry);
      }
      entry.windows.push(window);
    }
  }

  for (const entry of index.values()) {
    entry.windows.sort((a, b) => a.deployedAt - b.deployedAt);
    let running = -Infinity;
    entry.maxEnd = entry.windows.map((w) => {
      running = Math.max(running, w.stoppedAt ?? Infinity);
      return running;
    });
  }

  return (controllerId, atMs) => {
    const entry = index.get(controllerId);
    if (!entry || !Number.isFinite(atMs)) return null;

    // The last window that had already started at `atMs`. Everything after it
    // starts later and cannot contain the instant.
    let lo = 0;
    let hi = entry.windows.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (entry.windows[mid].deployedAt <= atMs) lo = mid + 1;
      else hi = mid;
    }

    let found: string | null = null;
    for (let i = lo - 1; i >= 0; i--) {
      if (entry.maxEnd[i] < atMs) break;
      const w = entry.windows[i];
      if (w.deployedAt > atMs) continue;
      if (w.stoppedAt !== null && atMs > w.stoppedAt) continue;
      // A second run claiming the same instant is an ambiguity, not a tie to
      // break — unless it is the same bot, which is one run listed twice.
      if (found !== null && found !== w.bot) return null;
      found = w.bot;
    }
    return found;
  };
}
