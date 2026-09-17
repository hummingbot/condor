import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";

import {
  DelegationSheet,
  type DelegationListing,
} from "@/components/agent/DelegationSheet";
import { isDelegationStatus } from "@/components/agent/delegationStatus";
import { DeploymentLedger } from "@/components/agent/lab/DeploymentLedger";
import { ExperimentDetail } from "@/components/agent/lab/RunOverview";
import { RunRail } from "@/components/agent/lab/RunRail";
import { OutsideWindow } from "@/components/agent/workspace/OutsideWindow";
import { api, type AgentRunRow } from "@/lib/api";

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
}) {
  return (
    // A bounded height rather than the page's: the rail scrolls beside its
    // body, which is what it was built to do, and a rail as tall as every run
    // would push the four disclosures below it off the end of the screen.
    <div className="flex h-[70vh] min-h-0">
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
 * What a rail row opens, for the three kinds that are not this screen.
 *
 * A loop run *is* the screen — selecting one re-scopes everything above — so it
 * says so rather than drawing a second copy of the answer stack inside the
 * disclosure that selected it.
 */
function RunBody({
  slug,
  run,
  onClearRun,
  onShowOlderRuns,
}: {
  slug: string;
  run: AgentRunRow | null;
  /** Put the selection back to the newest run — the sheet's way out. */
  onClearRun: () => void;
  onShowOlderRuns?: () => void;
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
    <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
      Session {run.number} is the run on this screen — its vitals, its last
      decision and what it deployed are above.
    </p>
  );
}
