import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { isLiveRun } from "@/components/agent/lab/runs";
import {
  alertsFor,
  journalNamesDeploy,
  type WorkspaceAlert,
} from "@/components/agent/workspace/views";
import { useOverdueSeconds } from "@/hooks/useOverdueSeconds";
import {
  api,
  type AgentPerformance,
  type AgentRunRow,
  type DeploymentRow,
  type RunningInstance,
} from "@/lib/api";
import { parseJournal, type Decision, type ParsedJournal } from "@/lib/parse-agent";

/**
 * One run's whole reading: its journal, actions and session-executors responses
 * are read once here and handed out as alerts, decisions, journal, deployments,
 * perf, pnlSeries and sessionNum.
 *
 * Still three queries and still the tick spine's and the detail bands' own, key
 * for key, so react-query hands every caller the same cache entries and the
 * whole screen makes one round of requests.
 *
 * The journal's and the action log's polling is set here, once, for the same
 * reason (CORR-369): react-query polls a shared key at the shortest interval
 * among its observers, so this declaration refreshes the spine, the Detail band
 * and the actions table too — which is why none of them declares one. Gated on
 * a live run, so a finished run costs no polls at all; without it the last
 * decision, the failed-action alert and the spine froze at page open while the
 * countdown kept moving.
 */
export function useRunReading({
  slug,
  sslug,
  run,
  instance,
}: {
  slug: string;
  sslug: string | null;
  run: AgentRunRow | null;
  instance: RunningInstance | null;
}): {
  alerts: WorkspaceAlert[];
  /** The run's decisions, newest last — the journal's own order. */
  decisions: Decision[];
  /** The whole journal, for the bands that want its metrics and its summary. */
  journal: ParsedJournal | null;
  deployments: DeploymentRow[];
  /** What the run's records are worth — the vitals strip's own numbers. */
  perf: AgentPerformance | null;
  /** Realized PnL over the run, derived from the bots' own history. */
  pnlSeries: { timestamp: string; pnl: number }[] | null;
  /** 0 when the scope has no session run, which is what gates every query. */
  sessionNum: number;
} {
  const sessionNum = run && run.kind === "session" && sslug ? run.number : 0;
  const enabled = !!sslug && sessionNum > 0;
  const liveInterval = run && isLiveRun(run) ? LIVE_RUN_REFETCH_MS : false;

  const { data: journalData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, sslug!, sessionNum),
    enabled,
    refetchInterval: liveInterval,
  });

  const { data: actionsData } = useQuery({
    queryKey: ["session-actions", slug, sslug, sessionNum],
    queryFn: () => api.getSessionActions(slug, sslug!, sessionNum),
    enabled,
    refetchInterval: liveInterval,
  });

  const { data: perfData } = useQuery({
    queryKey: ["strategy-session-executors", slug, sslug, sessionNum],
    queryFn: () => api.getStrategySessionExecutors(slug, sslug!, sessionNum),
    enabled,
    // Gated on the run, not on `instance`: the strategy's engine may be running
    // a different session than the one on screen (PERF-384).
    refetchInterval: liveInterval,
  });

  // Hoisted rather than reached through in the dependency list: the compiler
  // infers the whole `journalData` as the dependency and refuses to preserve a
  // memo whose declared one is narrower.
  const journalContent = journalData?.content;
  const journal = useMemo(
    () => (journalContent ? parseJournal(journalContent) : null),
    [journalContent],
  );
  const decisions = journal?.decisions ?? EMPTY_DECISIONS;

  // The overdue tick is the only alert that changes on its own. Its clock is
  // the lateness itself, not the time (PERF-372): a loop that is on time reads
  // -1 every second and re-renders nothing here, where a raw clock re-rendered
  // the whole screen once a second to print the same alerts.
  const overdueSec = useOverdueSeconds(instance);

  const actions = actionsData?.actions;
  const deployments = perfData?.deployments;
  const alerts = useMemo(
    () =>
      alertsFor({
        actions: actions ?? [],
        deployments: (deployments ?? []).length,
        journalNamesDeploy: journalNamesDeploy(decisions),
        loop: instance,
        nowSec: nowSecFor(instance, overdueSec),
      }),
    [actions, deployments, decisions, instance, overdueSec],
  );

  return {
    alerts,
    decisions,
    journal,
    deployments: deployments ?? [],
    perf: perfData?.performance ?? null,
    pnlSeries: perfData?.pnl_series ?? null,
    sessionNum,
  };
}

/** How often a live run's journal and action log are re-read — the vitals' own cadence. */
const LIVE_RUN_REFETCH_MS = 10_000;

/** One frozen empty list, so "no journal yet" is a stable identity. */
const EMPTY_DECISIONS: Decision[] = [];

/**
 * A `nowSec` that reads back as `overdueSec` in {@link alertsFor}'s rule.
 *
 * Half a second past the whole count, because the rule guards on `late > 0`:
 * the first second of lateness is count 0, and `due + 0` would drop the alert.
 * Not late (-1) maps to the due time itself, which the same guard reads as on
 * time.
 */
function nowSecFor(
  loop: { last_tick_at: number; frequency_sec: number } | null,
  overdueSec: number,
): number {
  if (!loop) return 0;
  const due = loop.last_tick_at + loop.frequency_sec;
  return overdueSec < 0 ? due : due + overdueSec + 0.5;
}
