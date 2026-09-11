import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  alertsFor,
  journalNamesDeploy,
  type WorkspaceAlert,
} from "@/components/agent/workspace/views";
import { useSeconds } from "@/hooks/useSeconds";
import {
  api,
  type AgentPerformance,
  type AgentRunRow,
  type DeploymentRow,
  type RunningInstance,
} from "@/lib/api";
import { parseJournal, type Decision, type ParsedJournal } from "@/lib/parse-agent";

/**
 * One run, in the three readings the screen is built out of.
 *
 * It started as the alert rules alone — a hook rather than a lump inside a
 * body, because the count had to be carried where the alerts were not. What
 * made it the run's whole reading is FEAT-119: the vitals, the last decision,
 * the chart and the deployed table are five facets of one run and they are on
 * one screen now, so the three responses they are cut from are read once here
 * and handed out, rather than each band re-declaring the query it wants.
 *
 * Still three queries and still the tick spine's and the detail bands' own, key
 * for key, so react-query hands every caller the same cache entries and the
 * whole screen makes one round of requests.
 */
export function useWorkspaceAlerts({
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

  const { data: journalData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, sslug!, sessionNum),
    enabled,
  });

  const { data: actionsData } = useQuery({
    queryKey: ["session-actions", slug, sslug, sessionNum],
    queryFn: () => api.getSessionActions(slug, sslug!, sessionNum),
    enabled,
  });

  const { data: perfData } = useQuery({
    queryKey: ["strategy-session-executors", slug, sslug, sessionNum],
    queryFn: () => api.getStrategySessionExecutors(slug, sslug!, sessionNum),
    enabled,
    refetchInterval: 10000,
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

  // A clock only while something is looping — an overdue tick is the only alert
  // that changes on its own, and a `Date.now()` in render is what the compiler
  // forbids anyway.
  const now = useSeconds(instance?.status === "running");

  const actions = actionsData?.actions;
  const deployments = perfData?.deployments;
  const alerts = useMemo(
    () =>
      alertsFor({
        actions: actions ?? [],
        deployments: (deployments ?? []).length,
        journalNamesDeploy: journalNamesDeploy(decisions),
        loop: instance,
        nowSec: now / 1000,
      }),
    [actions, deployments, decisions, instance, now],
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

/** One frozen empty list, so "no journal yet" is a stable identity. */
const EMPTY_DECISIONS: Decision[] = [];
