import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, ExternalLink, Layers, ScrollText } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";

import { AgentKnowledge } from "@/components/agent/AgentKnowledge";
import { PerformancePanel } from "@/components/agent/AgentOverviewTab";
import { SnapshotDetail } from "@/components/agent/AgentSessionContent";
import { DeploymentLedger } from "@/components/agent/lab/DeploymentLedger";
import { ExperimentDetail, RunOverview } from "@/components/agent/lab/RunOverview";
import { RunRail } from "@/components/agent/lab/RunRail";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { StrategyWorkbench } from "@/components/agent/StrategyWorkbench";
import { isKnowledgeTab } from "@/components/agent/knowledgeTabs";
import { LoopBar } from "@/components/agent/workspace/LoopBar";
import { NowView } from "@/components/agent/workspace/NowView";
import { useWorkspaceAlerts } from "@/components/agent/workspace/useWorkspaceAlerts";
import { WorkspaceHeader } from "@/components/agent/workspace/WorkspaceHeader";
import { WorkspaceSpine } from "@/components/agent/workspace/WorkspaceSpine";
import {
  parseWorkspace,
  pickRun,
  pickStrategy,
  runsRedirect,
  spineSectionFor,
  strategyRedirect,
  type WorkspaceViewId,
} from "@/components/agent/workspace/views";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { api, type AgentRunRow } from "@/lib/api";

/**
 * One agent, one screen, one route (FEAT-103).
 *
 * The path from a conversation to "Brigado deployed six controllers and they
 * are up $64" used to be six navigations across four pages — the agent page
 * (which opened on an `AGENT.md` dump), the strategy page, the Lab and the chat
 * — and the way back did not exist: the nav's Agents entry goes to the chat, so
 * "Back to Agents" was a link to a conversation and the Lab had no back control
 * at all.
 *
 * So there is one route now, and every state it can be in is a query parameter:
 * `?view=` names the section, `?strategy=` the scope, `?run=` and `?tick=` the
 * moment. That grammar is not invented here — the Lab already put
 * `?strategy=&run=&tick=` in the URL (FEAT-099) and defended it in its own
 * docstring; this generalises it upward over the agent so that a tick stays a
 * *selection* rather than becoming a destination you navigate to.
 *
 * The bodies are all imported unchanged. Four pages were four *shells* around
 * components that were already host-agnostic — `StrategyWorkbench` was hosted
 * by a page and by the chat, `AgentKnowledge` by a page and by the chat, and
 * the Lab's bands are exports of `AgentSessionContent` — so this replaces the
 * shells and keeps every body.
 *
 * Everything the URL says is read by `workspace/views.ts`, never in here: this
 * page reads more parameters than any page before it, and the containment is
 * that none of the rules for reading them live in JSX.
 */
export function AgentWorkspace() {
  const { slug = "" } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);

  const url = useMemo(() => parseWorkspace(searchParams), [searchParams]);

  /**
   * Write a selection into the URL.
   *
   * `replace` for a section change — reading down the sections is not nine
   * steps of history to press Back through — and a real push for a scope, a run
   * or a tick, which is what makes Back move one level shallower from any depth
   * without ever leaving the agent.
   */
  const setParams = useCallback(
    (
      next: Record<string, string | number | null>,
      options?: { replace?: boolean },
    ) => {
      const params = new URLSearchParams(searchParams);
      for (const [key, value] of Object.entries(next)) {
        if (value === null || value === "") params.delete(key);
        else params.set(key, String(value));
      }
      // `view=now` is the default, so it is never spelled out: the shortest URL
      // that lands somewhere is the one people paste.
      if (params.get("view") === "now") params.delete("view");
      params.delete("tab");
      setSearchParams(params, options);
    },
    [searchParams, setSearchParams],
  );

  const selectView = useCallback(
    (view: WorkspaceViewId) => setParams({ view }, { replace: true }),
    [setParams],
  );

  /**
   * A beat is a selection, not a destination.
   *
   * Clicking one sets `?tick=` and swaps the body to that tick without touching
   * the route — which is the whole argument for the query-parameter grammar.
   * Clearing it puts back the run it belongs to, so the spine's `Run` button is
   * the way up from a tick to its overview.
   */
  const selectTick = useCallback(
    (tick: number | null, from: WorkspaceViewId) =>
      setParams({
        tick,
        view: tick === null ? (from === "tick" ? "runs" : from) : "tick",
      }),
    [setParams],
  );

  // One `["agent", slug]` and one `["agent-runs", slug]` for the whole screen.
  // The header, the loop bar and the body all want them; react-query dedupes
  // the keys, which is the only reason three regions polling at 5s is one poll.
  const {
    data: agent,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  const { data: runs = [] } = useQuery({
    queryKey: ["agent-runs", slug],
    queryFn: () => api.getAgentRuns(slug),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  // Hoisted rather than reached through in the dependency lists: the compiler
  // infers the whole `agent` as the dependency and refuses to preserve a memo
  // whose declared one is narrower.
  const strategies = agent?.strategies;
  const sslug = useMemo(
    () => pickStrategy(strategies ?? [], runs, url.strategy),
    [strategies, runs, url.strategy],
  );
  const selectedRun = useMemo(
    () => pickRun(runs, sslug, url.run),
    [runs, sslug, url.run],
  );

  const scopedRuns = useMemo(
    () => (sslug ? runs.filter((r) => r.strategy_slug === sslug) : runs),
    [runs, sslug],
  );

  const { data: strategy = null } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug!),
    enabled: !!slug && !!sslug,
    refetchInterval: 5000,
  });

  // The live engine behind the selected run, for the cadence and the countdown
  // — the two facts a run row deliberately does not carry.
  const instances = strategy?.instances;
  const runAgentId = selectedRun?.agent_id;
  const instance = useMemo(
    () => instances?.find((i) => i.agent_id === runAgentId) ?? null,
    [instances, runAgentId],
  );

  // What this run wants a person for. Read at the page level rather than inside
  // Now, because the spine carries the count on every view — an alert you have
  // to open a section to discover is not one — and the three queries behind it
  // are the tick spine's and the run overview's own, so they cost one round.
  const { alerts, decisions, deployments, sessionNum } = useWorkspaceAlerts({
    slug,
    sslug,
    run: selectedRun,
    instance,
  });

  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  const deleteAgentMutation = useMutation({
    mutationFn: () => api.deleteAgent(slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate("/");
    },
  });

  /**
   * This surface cannot send a message, so it navigates to the workspace at `/`
   * carrying the request (FEAT-092). The encoding is not optional: an opener
   * carries backticks, parens and quotes.
   */
  const askAgent = useCallback(
    (text?: string) =>
      navigate(
        `/?agent=${encodeURIComponent(slug)}${
          text ? `&ask=${encodeURIComponent(text)}` : ""
        }`,
      ),
    [navigate, slug],
  );

  if (error && !agent) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="max-w-sm rounded-lg border border-red-500/30 bg-[var(--color-surface)] p-8 text-center">
          <AlertCircle className="mx-auto mb-3 h-10 w-10 text-[var(--color-red)]" />
          <h2 className="mb-1 text-lg font-semibold">Failed to Load Agent</h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            {error instanceof Error ? error.message : "An unexpected error occurred."}
          </p>
          <Link
            to="/"
            className="mt-4 inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading || !agent) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const isRunning = (agent.strategies || []).some((st) => st.status === "running");
  const view = url.view;

  const body = isKnowledgeTab(view) ? (
    /* Keyed on the section so a playbook left open in Skills does not follow
       the reader into Memories — the reset `AgentKnowledge`'s own tab click
       does, for a host that clicks nothing. */
    <AgentKnowledge
      key={view}
      slug={agent.slug}
      layout="bare"
      tab={view}
      onAskAgent={askAgent}
      routinesAction={
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            onClick={() => setShowRoutinesBrowser(true)}
            className="flex shrink-0 items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
            title="Every run these routines produced, and their reports"
          >
            <ScrollText className="h-3 w-3" /> Reports
          </button>
          <Link
            to={`/routines?agent=${agent.slug}`}
            className="flex shrink-0 items-center gap-1 px-1 py-1 text-[11px] text-[var(--color-text-muted)]/70 transition-colors hover:text-[var(--color-text)]"
            title="The full library, on its own page"
          >
            <ExternalLink className="h-3 w-3" /> Full library
          </Link>
        </div>
      }
      onOpenStrategy={(strategySlug) =>
        setParams({ view: "playbook", strategy: strategySlug })
      }
    />
  ) : !sslug ? (
    <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
      This agent has no strategies yet, so there is no loop to look at.
    </p>
  ) : view === "money" ? (
    <PerformancePanel slug={agent.slug} sslug={sslug} />
  ) : view === "tick" ? (
    /* One tick of one run, the same body the Lab renders — reached by clicking
       a beat on the spine above, which is why this view has no picker of its
       own. A dry run is a single tick in one file and has no beats. */
    selectedRun && selectedRun.kind === "session" && url.tick !== null ? (
      <SnapshotDetail
        slug={agent.slug}
        sslug={sslug}
        sessionNum={selectedRun.number}
        tick={url.tick}
      />
    ) : (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        Pick a beat on the spine above to read the tick it wrote.
      </p>
    )
  ) : view === "fleet" ? (
    <FleetView
      slug={agent.slug}
      sslug={sslug}
      run={selectedRun}
    />
  ) : view === "runs" ? (
    <RunsView
      slug={agent.slug}
      runs={runs}
      strategyFilter={url.strategy}
      onStrategyFilter={(next) =>
        setParams({ strategy: next, run: null, tick: null })
      }
      selected={selectedRun}
      onSelectRun={(run) =>
        setParams({ strategy: run.strategy_slug, run: run.run_id, tick: null })
      }
      tick={url.tick}
      onSelectTick={(next) => selectTick(next, "runs")}
      serverName={(strategy?.config?.server_name as string) || ""}
      controllerIds={instance ? [instance.agent_id] : undefined}
    />
  ) : view === "now" ? (
    <NowView
      slug={agent.slug}
      sslug={sslug}
      sessionNum={sessionNum}
      alerts={alerts}
      decisions={decisions}
      deployments={deployments}
      instance={instance}
      onOpenTick={(next) => selectTick(next, "now")}
    />
  ) : (
    /* The Runs band comes off here — the spine's Runs entry is the door now,
       and a band that duplicates it inside the body is the second door this
       feature exists to remove. */
    <StrategyWorkbench
      slug={agent.slug}
      sslug={sslug}
      showRuns={false}
      onDeleted={() => setParams({ view: "strategies", strategy: null })}
    />
  );

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <WorkspaceHeader
        agent={agent}
        strategy={strategy}
        isRunning={isRunning}
        onAskAgent={() => askAgent()}
        onDelete={() => setShowDeleteConfirm(true)}
      />

      <LoopBar
        slug={agent.slug}
        strategies={agent.strategies ?? []}
        sslug={sslug}
        onSelectStrategy={(next) =>
          setParams({ strategy: next, run: null, tick: null })
        }
        runs={scopedRuns}
        run={selectedRun}
        onSelectRun={(runId) => setParams({ run: runId, tick: null })}
        instance={instance}
        tick={url.tick}
        onSelectTick={(next) => selectTick(next, view)}
      />

      <div className="flex min-h-0 flex-1">
        <WorkspaceSpine
          current={spineSectionFor(view)}
          onSelect={selectView}
          alertCount={alerts.length}
        />
        <div
          className={`min-w-0 flex-1 ${
            view === "runs" ? "flex min-h-0" : "overflow-y-auto p-4"
          }`}
        >
          {body}
        </div>
      </div>

      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Agent"
        isPending={deleteAgentMutation.isPending}
        isError={deleteAgentMutation.isError}
        errorText="Failed to delete agent. It may have running strategies."
        onConfirm={() => deleteAgentMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete <strong className="text-[var(--color-text)]">{agent.name}</strong> and
        all its strategies? This cannot be undone.
      </ConfirmDialog>
    </div>
  );
}

/**
 * Every run of every strategy, and every tick of one — the Lab's whole job.
 *
 * The rail and the overview are the Lab's own bodies, imported unchanged: what
 * the Lab was, minus the page around it. Its header is gone because the loop bar
 * above already says which run this is and whether it is still going, which the
 * Lab could not — it had no agent name in it at all.
 */
function RunsView({
  slug,
  runs,
  strategyFilter,
  onStrategyFilter,
  selected,
  onSelectRun,
  tick,
  onSelectTick,
  serverName,
  controllerIds,
}: {
  slug: string;
  runs: AgentRunRow[];
  strategyFilter: string | null;
  onStrategyFilter: (sslug: string | null) => void;
  selected: AgentRunRow | null;
  onSelectRun: (run: AgentRunRow) => void;
  tick: number | null;
  onSelectTick: (tick: number | null) => void;
  serverName: string;
  controllerIds?: string[];
}) {
  return (
    <>
      <RunRail
        runs={runs}
        strategyFilter={strategyFilter}
        onStrategyFilter={onStrategyFilter}
        selectedKey={
          selected ? `${selected.strategy_slug}:${selected.run_id}` : null
        }
        onSelectRun={onSelectRun}
        isLoading={false}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {!selected ? (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
            This agent has no runs yet.
          </p>
        ) : selected.kind === "experiment" ? (
          <ExperimentDetail
            slug={slug}
            sslug={selected.strategy_slug}
            number={selected.number}
          />
        ) : tick !== null ? (
          /* A `?tick=` on the runs view is the Lab's own address, and the
             redirect from `/agents/:slug/runs` lands on it with its query
             string intact — so it has to read the tick, not the overview. */
          <SnapshotDetail
            slug={slug}
            sslug={selected.strategy_slug}
            sessionNum={selected.number}
            tick={tick}
          />
        ) : (
          <RunOverview
            slug={slug}
            sslug={selected.strategy_slug}
            sessionNum={selected.number}
            serverName={serverName}
            controllerIds={controllerIds}
            isLiveSession={
              selected.status === "running" || selected.status === "paused"
            }
            onSelectTick={onSelectTick}
          />
        )}
      </div>
    </>
  );
}

/**
 * What this run put into the world, and the door to the rest of the fleet.
 *
 * `DeploymentLedger` (FEAT-100) reads the same `strategy-session-executors`
 * response the run overview folds, so this view costs no query of its own — and
 * the two can never disagree about what was deployed.
 */
function FleetView({
  slug,
  sslug,
  run,
}: {
  slug: string;
  sslug: string;
  run: AgentRunRow | null;
}) {
  const sessionNum = run && run.kind === "session" ? run.number : 0;
  const { data } = useQuery({
    queryKey: ["strategy-session-executors", slug, sslug, sessionNum],
    queryFn: () => api.getStrategySessionExecutors(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
    refetchInterval: 10000,
  });

  return (
    <div className="space-y-4">
      <DeploymentLedger
        rows={data?.deployments ?? []}
        runKey={`${slug}.${sslug}`}
        sessionNum={sessionNum || undefined}
      />
      {/* The gesture the strategy page used to make — this agent's whole
          history, beside everything else that is trading (FEAT-096). */}
      <Link
        to={`/bots?scope=agent:${encodeURIComponent(`${slug}.${sslug}`)}`}
        className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
      >
        <Layers className="h-3.5 w-3.5" /> See this strategy beside the rest of
        the fleet
      </Link>
    </div>
  );
}

/**
 * `/agents/:slug/runs` — the Lab's address, kept resolving.
 *
 * A redirect and not a deletion: it is in notification payloads, in the chat's
 * route facts and in whatever anyone has bookmarked. The query string travels
 * with it, so `?strategy=&run=&tick=` lands on exactly the run and the tick it
 * always did — the Lab's grammar is a subset of the workspace's, which is what
 * made the merge possible at all.
 */
export function AgentRunsRedirect() {
  const { slug = "" } = useParams<{ slug: string }>();
  const { search } = useLocation();
  return <Navigate to={runsRedirect(slug, search)} replace />;
}

/** `/agents/:slug/strategies/:sslug` — the strategy page's address, likewise. */
export function AgentStrategyRedirect() {
  const { slug = "", sslug = "" } = useParams<{ slug: string; sslug: string }>();
  const { search } = useLocation();
  return <Navigate to={strategyRedirect(slug, sslug, search)} replace />;
}
