import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ExternalLink } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import {
  DelegationSheet,
  type DelegationListing,
} from "@/components/agent/DelegationSheet";
import { isDelegationStatus } from "@/components/agent/delegationStatus";
import { DeploymentLedger } from "@/components/agent/lab/DeploymentLedger";
import { ExperimentDetail } from "@/components/agent/lab/ExperimentDetail";
import { RunRail } from "@/components/agent/lab/RunRail";
import { actionsByTick, isLiveRun } from "@/components/agent/lab/runs";
import { SessionCanvasPanel } from "@/components/agent/session/SessionCanvasPanel";
import { SessionExecutors } from "@/components/agent/session/SessionExecutors";
import { OutsideWindow } from "@/components/agent/workspace/OutsideWindow";
import { api, type AgentRunRow } from "@/lib/api";
import { parseJournal } from "@/lib/parse-agent";

/**
 * The Runs band: every run this agent has had, beside what the selected one opens.
 *
 * The band draws the rail and the body; the host keeps the rail's *window*
 * (FEAT-111) — the page size, the widening and the `["agent-runs", …]` query —
 * because the index prints the `+` off it and the loop bar scopes off the same
 * runs. So the band is handed `hasMore`/`onShowMore` the way `RunRail` is,
 * rather than owning a second copy of the limit.
 */
export function RunsBand({
  slug,
  runs,
  selectedRun,
  strategyFilter,
  onStrategyFilter,
  onSelectRun,
  onClearRun,
  hasMore,
  onShowMore,
  onShowOlderRuns,
  onOpenTick,
  onShowNow,
  serverName,
  controllerIds,
}: {
  slug: string;
  runs: AgentRunRow[];
  selectedRun: AgentRunRow | null;
  /** `?strategy=` as the URL has it — the rail's filter, not the resolved scope. */
  strategyFilter: string | null;
  onStrategyFilter: (next: string | null) => void;
  onSelectRun: (run: AgentRunRow) => void;
  /** Put the selection back to the newest run — the sheet's way out. */
  onClearRun: () => void;
  hasMore: boolean;
  onShowMore: () => void;
  /** Set when the scoped strategy's runs are outside the window (CORR-376). */
  onShowOlderRuns?: () => void;
  /** Open one tick of a session over the screen — selecting the run with it. */
  onOpenTick: (run: AgentRunRow, tick: number) => void;
  /** Where the selected run's vitals and last decision are: the Now tab. */
  onShowNow: () => void;
  /** The agent's own server, where the selected session's market chart reads. */
  serverName: string;
  /** The controllers whose executors the selected session's chart streams. */
  controllerIds: string[];
}) {
  return (
    // The tab's whole height: the rail scrolls beside its body, which is what
    // it was built to do.
    <div className="flex h-full min-h-0">
      <RunRail
        runs={runs}
        strategyFilter={strategyFilter}
        onStrategyFilter={onStrategyFilter}
        selectedKey={
          selectedRun ? `${selectedRun.strategy_slug}:${selectedRun.run_id}` : null
        }
        onSelectRun={onSelectRun}
        isLoading={false}
        hasMore={hasMore}
        onShowMore={onShowMore}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <RunBody
          slug={slug}
          run={selectedRun}
          onClearRun={onClearRun}
          onShowOlderRuns={onShowOlderRuns}
          onOpenTick={onOpenTick}
          onShowNow={onShowNow}
          serverName={serverName}
          controllerIds={controllerIds}
        />
      </div>
    </div>
  );
}

/**
 * A rail row, as the delegation sheet reads a history row.
 *
 * The row carries the record's *listing* fields and none of its bodies, which
 * is exactly the shape `DelegationSheet` was built to open — it fetches the
 * record itself when the caller has no body, and that is how a task recorded by
 * a long-dead process is still readable. The status is narrowed rather than
 * cast: a record written by a newer build could name a state this dashboard
 * cannot colour, and `unknown` is the honest cell for it.
 */
function delegationTask(run: AgentRunRow, agent: string): DelegationListing {
  return {
    task_id: run.id,
    agent,
    task: run.title,
    status: isDelegationStatus(run.status) ? run.status : "unknown",
    kind: run.execution_mode === "consult" ? "consult" : "delegate",
    started_at: run.started_at ?? 0,
  };
}

/**
 * A conversation, as a run: what it deployed, and a door to what it said.
 *
 * A chat is one of the four kinds of run this rail lists (FEAT-111) and it is
 * the one whose *body* lives somewhere else — the transcript is the chat's, and
 * rebuilding a wide surface inside a disclosure is what FEAT-103's alternative
 * D argued against. But what it **did** is a ledger in the same shape every
 * other run's is (FEAT-110), and that is the half this screen can answer: the
 * row used to say "read it in the chat" and stop, which left the one question
 * an agent's page exists for unanswered for a quarter of its runs.
 *
 * `predates_ledger` is why the empty case is not one sentence. *Deployed
 * nothing* and *ran before Condor wrote down what a chat deployed* look
 * identical on screen and are not the same answer, and telling a reader the
 * first about the second would be a confident lie.
 */
function ConversationRun({ run }: { run: AgentRunRow }) {
  const { data, isLoading } = useQuery({
    queryKey: ["conversation-deployments", run.id],
    queryFn: () => api.getConversationDeployments(run.id),
    enabled: !!run.id,
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="min-w-0 truncate font-medium">
          {run.title || "This chat"}
        </span>
        <Link
          to={`/?conversation=${encodeURIComponent(run.id)}`}
          className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] underline-offset-2 transition-colors hover:text-[var(--color-primary)] hover:underline"
        >
          Read it in the chat <ExternalLink className="h-3 w-3" />
        </Link>
      </div>

      {isLoading ? (
        <div className="flex h-24 items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
        </div>
      ) : data?.predates_ledger ? (
        <p className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-xs text-[var(--color-text-muted)]">
          This chat ran before Condor recorded what a conversation deployed, so
          there is nothing to show — which is not the same as it having deployed
          nothing.
        </p>
      ) : (
        <DeploymentLedger rows={data?.deployments ?? []} />
      )}
    </div>
  );
}

/**
 * A loop session's turns: every tick, newest first, with what it did.
 *
 * Selecting a session re-scopes the whole screen, but the Now tab reads its
 * *last* decision only — so without this a four-tick run showed one tick and a
 * sentence pointing elsewhere. Each row opens that tick's snapshot over the
 * screen. The journal and the action log are the keys `TickSpine` and the Now
 * tab already read, so a run in scope costs no second fetch.
 *
 * Headed by the two readings no other tab draws (ARCH-427, which retired the
 * Detail tab that used to hold them): the agent's canvas — its thesis, as it
 * last revised it — and the session's market chart, every executor it ran on
 * the pair's candles with the ticks as bubbles that open them. Both draw
 * nothing for a session that has neither.
 */
function SessionTicks({
  slug,
  run,
  onOpenTick,
  onShowNow,
  serverName,
  controllerIds,
}: {
  slug: string;
  run: AgentRunRow;
  onOpenTick: (run: AgentRunRow, tick: number) => void;
  onShowNow: () => void;
  serverName: string;
  controllerIds: string[];
}) {
  const sslug = run.strategy_slug;
  const sessionNum = run.number;
  const { data: journalData, isLoading } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });
  const { data: actionsData } = useQuery({
    queryKey: ["session-actions", slug, sslug, sessionNum],
    queryFn: () => api.getSessionActions(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });

  // Hoisted: the compiler infers `journalData` as the dependency and will not
  // preserve a memo that declares a narrower one.
  const journalContent = journalData?.content;
  const ticks = useMemo(
    () =>
      journalContent ? [...parseJournal(journalContent).ticks].reverse() : [],
    [journalContent],
  );
  const byTick = useMemo(
    () => actionsByTick(actionsData?.actions ?? []),
    [actionsData?.actions],
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium">
          Session {run.number}
          <span className="ml-2 font-mono text-xs text-[var(--color-text-muted)]">
            {ticks.length} tick{ticks.length === 1 ? "" : "s"}
          </span>
        </span>
        <button
          type="button"
          onClick={onShowNow}
          className="text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)] hover:underline"
        >
          Vitals and last decision on Now →
        </button>
      </div>

      <SessionCanvasPanel slug={slug} sslug={sslug} sessionNum={sessionNum} />
      <SessionExecutors
        chartsOnly
        slug={slug}
        sslug={sslug}
        sessionNum={sessionNum}
        serverName={serverName}
        controllerIds={controllerIds}
        onSnapshotClick={(tick) => onOpenTick(run, tick)}
        isLiveSession={isLiveRun(run)}
      />

      {isLoading ? (
        <div className="flex h-24 items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
        </div>
      ) : ticks.length === 0 ? (
        <p className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-xs text-[var(--color-text-muted)]">
          No ticks recorded for this run yet.
        </p>
      ) : (
        <ol data-run-ticks className="divide-y divide-[var(--color-border)] rounded-lg border border-[var(--color-border)]">
          {ticks.map((entry) => {
            const deeds = byTick.get(entry.tick) ?? [];
            const failed = deeds.some((d) => !d.ok);
            return (
              <li key={entry.tick}>
                <button
                  type="button"
                  data-run-tick={entry.tick}
                  onClick={() => onOpenTick(run, entry.tick)}
                  className="group flex w-full items-start gap-3 px-3 py-2.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
                >
                  <span
                    className={`mt-0.5 shrink-0 font-mono text-xs font-bold tabular-nums ${
                      failed ? "text-[var(--color-red)]" : "text-[var(--color-primary)]"
                    }`}
                  >
                    #{entry.tick}
                  </span>
                  <span className="min-w-0 flex-1 space-y-1">
                    <span className="flex items-baseline gap-2">
                      <span className="min-w-0 flex-1 text-sm">
                        {entry.summary || (
                          <span className="text-[var(--color-text-muted)]">
                            No summary written
                          </span>
                        )}
                      </span>
                      <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-muted)]">
                        {entry.timestamp}
                      </span>
                    </span>
                    {deeds.length > 0 && (
                      <span className="flex flex-wrap gap-1">
                        {deeds.map((d, i) => (
                          <span
                            key={i}
                            title={d.error || d.summary}
                            className={`rounded px-1.5 py-px text-[10px] ${
                              d.ok
                                ? "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                                : "bg-red-500/10 text-[var(--color-red)]"
                            }`}
                          >
                            {d.summary}
                          </span>
                        ))}
                      </span>
                    )}
                  </span>
                  <ChevronRight className="mt-1 h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100" />
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/**
 * What a rail row opens: a session's ticks, an experiment's detail, a task's
 * sheet or a chat's ledger.
 */
function RunBody({
  slug,
  run,
  onClearRun,
  onShowOlderRuns,
  onOpenTick,
  onShowNow,
  serverName,
  controllerIds,
}: {
  slug: string;
  run: AgentRunRow | null;
  /** Put the selection back to the newest run — the sheet's way out. */
  onClearRun: () => void;
  onShowOlderRuns?: () => void;
  onOpenTick: (run: AgentRunRow, tick: number) => void;
  onShowNow: () => void;
  serverName: string;
  controllerIds: string[];
}) {
  if (!run && onShowOlderRuns) {
    return (
      <div className="py-8 text-center">
        <OutsideWindow onShowOlderRuns={onShowOlderRuns} />
      </div>
    );
  }
  if (!run) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        This agent has no runs yet.
      </p>
    );
  }
  if (run.kind === "delegation") {
    // The one place a background task is read already exists — the dock, the
    // fleet card and an agent's history all open this sheet — and a rail row is
    // the fourth caller, not a fourth copy.
    return <DelegationSheet task={delegationTask(run, slug)} onClose={onClearRun} />;
  }
  if (run.kind === "conversation") return <ConversationRun run={run} />;
  if (run.kind === "experiment") {
    return (
      <ExperimentDetail slug={slug} sslug={run.strategy_slug} number={run.number} />
    );
  }
  return (
    <SessionTicks
      slug={slug}
      run={run}
      onOpenTick={onOpenTick}
      onShowNow={onShowNow}
      serverName={serverName}
      controllerIds={controllerIds}
    />
  );
}
