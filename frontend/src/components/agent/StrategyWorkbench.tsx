import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, FileText, FlaskConical, Layers, ScrollText, Trash2, X, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import { AgentMarketStrip } from "@/components/agent/AgentMarketStrip";
import {
  InstanceCard,
  MarkdownEditor,
  PerformancePanel,
} from "@/components/agent/AgentOverviewTab";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { LoopPulse } from "@/components/agent/LoopPulse";
import { SessionReviewer } from "@/components/agent/SessionReviewer";
import { DiscardChangesDialog } from "@/components/editor/EditorDialogs";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api } from "@/lib/api";
import { groupExecutorsByMarket } from "@/lib/executor-overlays";

/**
 * A strategy, everywhere a strategy is shown.
 *
 * This was the body of `pages/StrategyDetail`, and only ever that: opening a
 * strategy meant leaving whatever you were doing for a page of its own. Beside
 * a conversation that is the wrong trade — you click a card the agent just told
 * you about and lose the agent — so the body moved here and the page became a
 * thin host for it, alongside a second host in the chat's workspace pane
 * (`StrategySheet`). One component, so the pane can never be the page's poorer
 * cousin: whatever you can do to a strategy, you can do wherever it is open.
 *
 * `dense` is the pane's half-row rather than a page's full width. It is a prop
 * and not a media query for the reason `AgentStrategies.dense` is: the *window*
 * is wide in both cases, so `lg:` is true in a 640px column and an eight-across
 * stat grid lands there as eight slivers.
 *
 * The reviewer, the playbook editor, the routine browser and the delete
 * confirmation all stay overlays here, above whichever host is on screen. They
 * are modal work — you are reading one session, or editing one file — and an
 * overlay inside a pane that is itself beside a chat is still the smallest
 * thing that can hold them.
 */
export function StrategyWorkbench({
  slug,
  sslug,
  dense = false,
  onDeleted,
}: {
  slug: string;
  sslug: string;
  /** Half a workspace row rather than a page: one column, tighter grids. */
  dense?: boolean;
  /** The host's move after a delete — pop the pane, or navigate the page. */
  onDeleted: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [reviewerSessionNum, setReviewerSessionNum] = useState<number | null>(null);
  // A tick to open the reviewer straight onto, when the caller had one (the
  // fleet band's deed line, and the loop pulse's beats, both carry one).
  const [reviewerSnapshotTick, setReviewerSnapshotTick] = useState<number | null>(null);
  const [reviewerKind, setReviewerKind] = useState<"session" | "experiment">("session");
  const [showStrategyModal, setShowStrategyModal] = useState(false);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  // Unsaved-edit guards for the Playbook/Learnings editors (CORR-093)
  const [playbookDirty, setPlaybookDirty] = useState(false);
  const [learningsDirty, setLearningsDirty] = useState(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug, sslug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
      onDeleted();
    },
  });

  // Close the strategy modal, dropping any unsaved-edit guards.
  const closeStrategyModal = useCallback(() => {
    setShowStrategyModal(false);
    setShowDiscardConfirm(false);
    setPlaybookDirty(false);
    setLearningsDirty(false);
  }, []);

  // Backdrop click, Escape and the X button all route through here: with
  // unsaved edits they ask for confirmation instead of silently discarding.
  const requestCloseStrategyModal = useCallback(() => {
    if (playbookDirty || learningsDirty) {
      setShowDiscardConfirm(true);
    } else {
      closeStrategyModal();
    }
  }, [playbookDirty, learningsDirty, closeStrategyModal]);

  // Close strategy modal on Escape (the discard dialog owns Escape while open)
  useEscapeKey(showStrategyModal && !showDiscardConfirm, requestCloseStrategyModal);

  const { data: strategy, isLoading, error } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug),
    enabled: !!slug && !!sslug,
    refetchInterval: 5000,
  });

  // Routine instances for ReportBrowser
  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  // Derive controller IDs from active instances for WS executor streaming
  const instances = useMemo(() => strategy?.instances || [], [strategy?.instances]);
  const hasRunning = instances.length > 0;
  const serverName = (strategy?.config?.server_name as string) || "";

  // Executor-mode ids: a running engine tags its own executors with its agent_id.
  // A bot-mode strategy adds nothing here — its controllers tag executors with
  // their own config ids — which is why the live charts below stayed empty for it.
  // The session reviewer widens this with the live bots' controller ids, which it
  // can resolve per session; here we widen it with the newest session's.
  const agentControllerIds = useMemo(
    () => instances.map((inst) => inst.agent_id).filter(Boolean),
    [instances],
  );

  // Depends on `strategy`, not `strategy?.sessions`: the compiler infers the
  // whole object anyway (the optional chain reads it), and claiming the
  // narrower dependency is what made this the one memo it refused to optimize.
  const latestSessionNum = useMemo(
    () => (strategy?.sessions?.length ? Math.max(...strategy.sessions.map((s) => s.number)) : 0),
    [strategy],
  );

  const { data: latestSessionPerf } = useQuery({
    queryKey: ["strategy-session-executors", slug, sslug, latestSessionNum],
    queryFn: () => api.getStrategySessionExecutors(slug, sslug, latestSessionNum),
    enabled: !!slug && !!sslug && latestSessionNum > 0,
    refetchInterval: 10000,
  });

  const controllerIds = useMemo(() => {
    const ids = new Set(agentControllerIds);
    const perf = latestSessionPerf?.performance;
    const live = new Set(perf?.bot_names ?? []);
    for (const c of perf?.controllers ?? []) {
      if (c.controller_id && live.has(c.bot_name)) ids.add(c.controller_id);
    }
    return Array.from(ids);
  }, [agentControllerIds, latestSessionPerf]);

  // Real-time executor data via WS
  const { executors: liveExecutors } = useAgentExecutors(
    hasRunning ? serverName : null,
    controllerIds,
  );

  // Group live executors by connector:pair for charts
  const chartGroups = useMemo(
    () => (serverName ? groupExecutorsByMarket(liveExecutors) : []),
    [liveExecutors, serverName],
  );

  // Session/experiment click -> open reviewer
  const handleSessionClick = useCallback((sessionNum: number, kind?: "session" | "experiment") => {
    setReviewerSessionNum(sessionNum);
    setReviewerSnapshotTick(null);
    setReviewerKind(kind || "session");
  }, []);

  /** A beat on the pulse strip is an address: land on that tick's snapshot. */
  const handleOpenTick = useCallback((sessionNum: number, tick: number) => {
    setReviewerSessionNum(sessionNum);
    setReviewerSnapshotTick(tick);
    setReviewerKind("session");
  }, []);

  /** Deep-linked from elsewhere (the fleet band's `openReviewer` state). */
  const openReviewerAt = useCallback(
    (sessionNum: number | null, snapshotTick: number | null) => {
      setReviewerSessionNum(sessionNum);
      setReviewerSnapshotTick(snapshotTick);
      setReviewerKind("session");
    },
    [],
  );
  useDeepLinkedReviewer(openReviewerAt);

  if (error && !strategy) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <div className="max-w-sm rounded-lg border border-red-500/30 bg-[var(--color-surface)] p-8 text-center">
          <AlertCircle className="mx-auto mb-3 h-10 w-10 text-[var(--color-red)]" />
          <h2 className="mb-1 text-lg font-semibold">Failed to Load Strategy</h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            {error instanceof Error ? error.message : "An unexpected error occurred."}
          </p>
        </div>
      </div>
    );
  }

  if (isLoading || !strategy) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const reviewerOpen = reviewerSessionNum !== null;
  const resolvedReviewerSession =
    reviewerSessionNum ?? (strategy.sessions.length > 0 ? strategy.sessions[0].number : 0);
  const liveInstance = instances.find((i) => i.status === "running") ?? instances[0] ?? null;

  const actionClass =
    "flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]";
  /** In a pane the labels are dropped for room; on a page they read normally. */
  const labelClass = dense ? "hidden" : "hidden sm:inline";

  return (
    <div className="w-full">
      {/* Header: identity on the left, everything you can do to it on the right */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className={`font-bold text-[var(--color-text)] ${dense ? "text-base" : "text-xl"}`}>
            <span className="text-[var(--color-text-muted)]">{slug}</span>
            <span className="mx-1 text-[var(--color-text-muted)]">/</span>
            {strategy.name}
          </h1>
          {strategy.description && (
            <p className={`mt-1 text-sm text-[var(--color-text-muted)] ${dense ? "line-clamp-2" : ""}`}>
              {strategy.description}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setShowStrategyModal(true)}
            className={actionClass}
            title="Playbook & Learnings"
          >
            <FileText className="h-3.5 w-3.5" />
            <span className={labelClass}>Playbook</span>
          </button>
          {strategy.experiments.length > 0 && (
            <button
              onClick={() =>
                handleSessionClick(
                  Math.max(...strategy.experiments.map((e) => e.number)),
                  "experiment",
                )
              }
              className={actionClass}
              title="Dry-run & run-once snapshots"
            >
              <FlaskConical className="h-3.5 w-3.5" />
              <span className={labelClass}>Dry runs ({strategy.experiments.length})</span>
            </button>
          )}
          {/* The way back into the fleet page (FEAT-096). The link the agent
              band's *Open session* is the other half of: a strategy's work
              is a grouping fact about the fleet, and this is where the reader
              goes to see it beside everything else that is trading. */}
          <button
            onClick={() => navigate(`/bots?scope=agent:${encodeURIComponent(`${slug}.${sslug}`)}`)}
            className={actionClass}
            title="See this strategy's bots and executors beside the rest of the fleet"
          >
            <Layers className="h-3.5 w-3.5" />
            <span className={labelClass}>View in fleet</span>
          </button>
          <button
            onClick={() => setShowRoutinesBrowser(true)}
            className={actionClass}
            title="Routines & Reports"
          >
            <ScrollText className="h-3.5 w-3.5" />
            <span className={labelClass}>Routines</span>
          </button>
          <button
            onClick={() => setShowDeleteConfirm(true)}
            disabled={strategy.status === "running"}
            className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-30"
            title={strategy.status === "running" ? "Stop strategy before deleting" : "Delete strategy"}
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span className={labelClass}>Delete</span>
          </button>
          <AgentControls
            slug={slug}
            sslug={sslug}
            status={strategy.status}
            defaultContext={strategy.default_trading_context || (strategy.config.trading_context as string) || ""}
            agentConfig={strategy.config}
          />
        </div>
      </div>

      {/* What the loop is doing — the first thing on the page, because it is
          the thing a strategy *is*. Above the money: the numbers are the
          outcome, and this is the mechanism that produced them. */}
      <div className="mb-4">
        <LoopPulse
          instance={liveInstance}
          status={strategy.status}
          config={strategy.config}
          onOpenTick={handleOpenTick}
        />
      </div>

      {/* Meta strip */}
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
          {strategy.sessions.length} session{strategy.sessions.length !== 1 ? "s" : ""}
        </span>
        {/* Said beside the sessions rather than only behind a button: a
            strategy whose whole history is one dry run used to read as one
            that had never run. */}
        {strategy.experiments.length > 0 && (
          <button
            onClick={() =>
              handleSessionClick(
                Math.max(...strategy.experiments.map((e) => e.number)),
                "experiment",
              )
            }
            className="flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-amber-400 transition-colors hover:bg-amber-500/20"
          >
            <FlaskConical className="h-3 w-3" />
            {strategy.experiments.length} dry run{strategy.experiments.length !== 1 ? "s" : ""}
          </button>
        )}
        <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
          {strategy.slug}
        </span>
        {strategy.agent_id && (
          <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
            {strategy.agent_id}
          </span>
        )}
      </div>

      {/* Market Context Strip */}
      {hasRunning && liveExecutors.length > 0 && (
        <div className="mb-6">
          <AgentMarketStrip serverName={serverName} executors={liveExecutors} />
        </div>
      )}

      {/* Live Executor Charts */}
      {hasRunning && chartGroups.length > 0 && (
        <div className="mb-6 space-y-4">
          <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            <Zap className="h-3.5 w-3.5" /> Live Executors
          </h3>
          {chartGroups.map(([key, group]) => (
            <ExecutorChart
              key={key}
              server={serverName}
              executors={group}
              connector={group[0].connector}
              tradingPair={group[0].trading_pair}
              height={dense ? 220 : 300}
            />
          ))}
        </div>
      )}

      {/* Running Instances */}
      {hasRunning && (
        <div className="mb-6 rounded-lg border border-emerald-500/20 bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-emerald-400">
            <Zap className="h-3.5 w-3.5" /> Active Sessions ({instances.length})
          </h3>
          <div className="space-y-3">
            {instances.map((inst) => (
              <InstanceCard key={inst.agent_id} instance={inst} />
            ))}
          </div>
        </div>
      )}

      {/* Performance Panel + Sessions table */}
      <div className={`mb-8 grid grid-cols-1 gap-6 ${dense ? "" : "lg:grid-cols-2"}`}>
        <PerformancePanel
          slug={slug}
          sslug={sslug}
          dense={dense}
          onSessionClick={handleSessionClick}
        />
      </div>

      {/* Playbook & Learnings Modal (near full-screen) */}
      {showStrategyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60"
            onClick={requestCloseStrategyModal}
          />
          {/* Modal panel */}
          <div className="relative z-10 flex h-[90vh] w-[95vw] max-w-7xl flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
            {/* Modal header */}
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">
                Playbook & Learnings — {strategy.name}
              </h3>
              <button
                onClick={requestCloseStrategyModal}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            {/* Modal content */}
            <div className="flex-1 overflow-y-auto p-6">
              <div className="grid h-full grid-cols-1 gap-6 lg:grid-cols-2">
                <MarkdownEditor
                  label="Playbook"
                  sublabel="strategy.md"
                  content={strategy.strategy_md}
                  onSave={(value) => api.updateStrategyMd(slug, sslug, value)}
                  invalidateKey={["strategy", slug, sslug]}
                  onDirtyChange={setPlaybookDirty}
                />
                <MarkdownEditor
                  label="Learnings"
                  sublabel="persists across sessions"
                  content={strategy.learnings}
                  onSave={(value) => api.updateStrategyLearnings(slug, sslug, value)}
                  invalidateKey={["strategy", slug, sslug]}
                  onDirtyChange={setLearningsDirty}
                />
              </div>
            </div>
          </div>
          {/* Unsaved-changes confirmation before discarding edits */}
          {showDiscardConfirm && (
            <DiscardChangesDialog
              fileName={
                playbookDirty && learningsDirty
                  ? "strategy.md & learnings"
                  : playbookDirty
                    ? "strategy.md"
                    : "learnings"
              }
              onDiscard={closeStrategyModal}
              onClose={() => setShowDiscardConfirm(false)}
            />
          )}
        </div>
      )}

      {/* Routines ReportBrowser (full-screen overlay; routines live at the agent
          level and are shared across strategies, so filter by the agent slug) */}
      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Strategy"
        isPending={deleteMutation.isPending}
        isError={deleteMutation.isError}
        errorText="Failed to delete strategy. It may be running."
        onConfirm={() => deleteMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete{" "}
        <strong className="text-[var(--color-text)]">{strategy.name}</strong>? This
        cannot be undone.
      </ConfirmDialog>

      {/* Session Reviewer Overlay */}
      {reviewerOpen && (strategy.sessions.length > 0 || strategy.experiments.length > 0) && (
        <SessionReviewer
          slug={slug}
          sslug={sslug}
          agentName={`${slug} / ${strategy.name}`}
          sessions={strategy.sessions}
          experiments={strategy.experiments}
          initialSessionNum={resolvedReviewerSession}
          initialKind={reviewerKind}
          initialSnapshotTick={reviewerSnapshotTick}
          serverName={serverName}
          controllerIds={controllerIds}
          onClose={() => {
            setReviewerSessionNum(null);
            setReviewerSnapshotTick(null);
          }}
        />
      )}
    </div>
  );
}

/**
 * Honour a `location.state` deep link into the reviewer, once.
 *
 * The fleet band navigates here with `{ openReviewer, sessionNum, snapshotTick }`
 * so a deed on that page lands on the tick that produced it. The state is
 * cleared on arrival: a reload, or a later back-navigation, must not re-open a
 * reviewer the reader has since closed.
 *
 * Both hosts are inside the router — the pane lives on the `/` route — so the
 * pane simply never carries this state and the effect never fires there.
 */
function useDeepLinkedReviewer(
  open: (sessionNum: number | null, snapshotTick: number | null) => void,
) {
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    const state = location.state as
      | { openReviewer?: boolean; sessionNum?: number; snapshotTick?: number }
      | null;
    if (state?.openReviewer) {
      open(state.sessionNum ?? null, state.snapshotTick ?? null);
      navigate(location.pathname + location.search, { replace: true, state: null });
    }
  }, [location.state, location.pathname, location.search, navigate, open]);
}
