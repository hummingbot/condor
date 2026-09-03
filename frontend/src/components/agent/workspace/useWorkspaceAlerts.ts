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
  type AgentRunRow,
  type DeploymentRow,
  type RunningInstance,
} from "@/lib/api";
import { parseJournal, type Decision } from "@/lib/parse-agent";

/**
 * What this run wants a person for, and the three readings it is derived from.
 *
 * A hook and not a lump inside `NowView` because the spine carries the count on
 * every view: an alert you have to open a section to discover is not one. The
 * three queries are the tick spine's and the run overview's own, key for key,
 * so react-query hands both callers the same cache entries and the whole screen
 * still makes one round of requests.
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
  deployments: DeploymentRow[];
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
  const decisions = useMemo(
    () => (journalContent ? parseJournal(journalContent).decisions : []),
    [journalContent],
  );

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

  return { alerts, decisions, deployments: deployments ?? [], sessionNum };
}
