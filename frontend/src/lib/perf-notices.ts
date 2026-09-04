/**
 * What the PnL chart says about where its curve came from.
 *
 * The picture cannot tell a sampled curve from a rebuilt one, and a chart that
 * looked identical while meaning something weaker would be the worst of the
 * available answers — so the notice is the whole disclosure. Lifted out of
 * `PerfBrowser` (ARCH-300): the selection is pure over its inputs, and inline
 * it was the one part of the browser no test could reach.
 */

import type { PerfSeriesSource } from "@/lib/perf-history";
import type { NodeKind, Population } from "@/lib/perf-tree";

/** The shape `PnlEvolutionChart` takes for its `notice` prop. */
export interface PerfNotice {
  label: string;
  detail?: string;
}

/** As much of a run-history response as the notice actually reads. */
export interface RunHistoryFacts {
  source: "snapshots" | "archive" | "none";
  points: number;
  /** Why, when the server has nothing. `null` is how the API says "no reason". */
  detail?: string | null;
}

export interface ChartNoticeInput {
  /** Which node the browser is reporting on. */
  scopeKind: NodeKind;
  population: Population;
  /** The finished run's own history, when the scope is on one. */
  runHistory?: RunHistoryFacts | null;
  /** Which of the three sources actually drew the series. */
  seriesSource: PerfSeriesSource;
  /** `/performance/history` presence on this API build; undefined while unprobed. */
  capabilitySupported?: boolean;
  /** The executor's own history query is in flight. */
  execHistoryLoading: boolean;
  /** That query rejected outright — a 4xx, or Condor itself failing. */
  execHistoryError: boolean;
  /**
   * Whether the upstream API answered at all.
   *
   * `/performance/history` reports an unreachable upstream in band — a 200
   * carrying `server_online: false` and no rows — so a transport failure is
   * indistinguishable from an empty table unless this is read.
   */
  execHistoryServerOnline?: boolean;
  /** The server's own words for why, when the request failed. */
  execHistoryErrorHint?: string;
  /** The fleet history stops short of the earliest deploy. */
  truncated: boolean;
}

/** Said rather than assumed: an executor scope with no sampled series of its own. */
const NO_EXECUTOR_SERIES: PerfNotice = {
  label: "no recorded series",
  detail:
    "The server records executor performance over time, but has none for this one — it closed before the table existed, or it lived less than one snapshot interval. The totals above are its own.",
};

/**
 * Said instead of absence, when the request for the series did not come back
 * (CORR-299).
 *
 * A fetch that failed establishes nothing about the executor, so reporting it
 * as "the server has none for this one" asserts a fact nobody checked — and it
 * is the one wording that stops the reader from retrying. Naming the failure
 * keeps the chart honest about which of the two it is.
 */
const historyUnavailable = (hint?: string): PerfNotice => ({
  label: "history unavailable",
  detail: `The request for this executor's recorded series failed${hint ? ` — ${hint}` : ""}, so whether the server has one is unknown. The fallback is drawn below; the totals above are its own.`,
});

/**
 * What the terminated chart is actually drawn from.
 *
 * It used to claim "closed outcomes" unconditionally, which was true when that
 * was the only thing it could draw. There are three sources now and the reader
 * has no other way to tell them apart: a run's own sampled history reads
 * exactly like a live one.
 */
export function terminatedNotice({
  population,
  runHistory,
}: Pick<ChartNoticeInput, "population" | "runHistory">): PerfNotice | undefined {
  if (population !== "terminated") return undefined;
  if (runHistory && runHistory.points > 0) {
    return runHistory.source === "archive"
      ? {
          label: "from the archived database",
          detail:
            "The server kept no performance snapshots this far back, so this curve is rebuilt from the run's archived trade table. It is trade-exact and has no unrealized series, because a closed trade has nothing left unrealized.",
        }
      : undefined;
  }
  return runHistory?.source === "none"
    ? {
        label: "no recorded history",
        detail: `The server has no stored history for this run${runHistory.detail ? ` — ${runHistory.detail}` : ""}. Its snapshot table only reaches so far back, and this run started before that. The steps below are what its executors closed, at the times they closed.`,
      }
    : {
        label: "closed outcomes",
        detail:
          "Drawn from each executor's close time and its final PnL, not from sampled history — nothing here is still open, so there is no unrealized series and no position to hold. Each step is what closed in that bucket.",
      };
}

/**
 * What an executor scope has to say about its own curve (FEAT-087).
 *
 * Three states, and the reader cannot tell them apart from the picture:
 *
 *  - **drawn from its own snapshots** — nothing to say. The curve is the
 *    executor's, sampled, and reads like any other.
 *  - **the server cannot record one** — `/performance/history` is not there.
 *    That is a property of *their API build*, not of this executor, and
 *    naming it is the difference between "there is nothing to show" and
 *    "upgrade and there will be".
 *  - **the route is there and this executor has no rows** — it closed before
 *    the table existed, or it lived less than one dump interval. Backfilling
 *    history for an executor that already closed is deliberately out of
 *    scope, so the honest answer is that it was never recorded.
 *
 * The fourth state is not an answer at all: nothing came back. Absence is a
 * claim about the executor and a failed request cannot support it, so the
 * unknown ones — the probe still in flight, the fetch that failed — either say
 * nothing or say that it failed (CORR-299).
 */
export function executorNotice({
  scopeKind,
  seriesSource,
  capabilitySupported,
  execHistoryLoading,
  execHistoryError,
  execHistoryServerOnline,
  execHistoryErrorHint,
}: Pick<
  ChartNoticeInput,
  | "scopeKind"
  | "seriesSource"
  | "capabilitySupported"
  | "execHistoryLoading"
  | "execHistoryError"
  | "execHistoryServerOnline"
  | "execHistoryErrorHint"
>): PerfNotice | undefined {
  if (scopeKind !== "executor" || seriesSource === "snapshots") return undefined;
  if (capabilitySupported === false) {
    return {
      label: "no recorded series",
      detail:
        "This API does not record executor performance over time, so there is no curve for this executor — only the totals above, which are its own. An API with the shared performance history draws the executor's own sampled series here.",
    };
  }
  // The probe has not answered yet, so the history query is still disabled and
  // `execHistoryLoading` is false: nothing is known about this executor, and
  // the absence copy below would be a guess dressed as a finding.
  if (capabilitySupported === undefined || execHistoryLoading) return undefined;
  // An unreachable upstream arrives in band as a 200 with `server_online:
  // false`; a 4xx or a Condor-side fault rejects the query instead. Neither
  // one looked at the executor's rows.
  if (execHistoryServerOnline === false || execHistoryError) {
    return historyUnavailable(execHistoryErrorHint);
  }
  return NO_EXECUTOR_SERIES;
}

/** The one notice the chart is given, whichever of the three applies. */
export function chartNotice(input: ChartNoticeInput): PerfNotice | undefined {
  if (input.scopeKind === "executor") return executorNotice(input);
  if (input.population === "terminated") return terminatedNotice(input);
  return input.truncated
    ? {
        label: "partial history",
        detail:
          "This fleet has more stored history than one chart may load at once, so the series starts later than the earliest deploy.",
      }
    : undefined;
}
