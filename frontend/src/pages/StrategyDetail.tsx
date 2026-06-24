import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileText, ScrollText, Trash2, X, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import { AgentMarketStrip } from "@/components/agent/AgentMarketStrip";
import {
  InstanceCard,
  MarkdownEditor,
  PerformancePanel,
} from "@/components/agent/AgentOverviewTab";
import { SessionReviewer } from "@/components/agent/SessionReviewer";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { api } from "@/lib/api";
import { groupExecutorsByMarket } from "@/lib/executor-overlays";

// ── Strategy Detail Page ──
//
// A strategy is a playbook that loops under an Agent. This page holds the rich
// operational view: live executors, sessions/experiments, controls, PnL and the
// strategy.md / learnings editors. The owning Agent's identity lives one level up
// at /agents/:slug.

export function StrategyDetail() {
  const { slug, sslug } = useParams<{ slug: string; sslug: string }>();
  const navigate = useNavigate();
  const location = useLocation();

  const queryClient = useQueryClient();
  const [reviewerSessionNum, setReviewerSessionNum] = useState<number | null>(null);
  const [reviewerKind, setReviewerKind] = useState<"session" | "experiment">("session");
  const [showStrategyModal, setShowStrategyModal] = useState(false);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug!, sslug!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      navigate(`/agents/${slug}`);
    },
  });

  // Check location.state for session-deep-linking (SessionReviewer nav)
  useEffect(() => {
    const state = location.state as { openReviewer?: boolean; sessionNum?: number } | null;
    if (state?.openReviewer) {
      setReviewerSessionNum(state.sessionNum ?? null);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.state, location.pathname, navigate]);

  // Close strategy modal on Escape
  useEffect(() => {
    if (!showStrategyModal) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setShowStrategyModal(false);
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [showStrategyModal]);

  const { data: strategy, isLoading } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug!, sslug!),
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
  const instances = strategy?.instances || [];
  const hasRunning = instances.length > 0;
  const serverName = (strategy?.config?.server_name as string) || "";

  const controllerIds = useMemo(
    () => instances.map((inst) => inst.agent_id).filter(Boolean),
    [instances],
  );

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
    setReviewerKind(kind || "session");
  }, []);

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

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-4">
        <button
          onClick={() => navigate(`/agents/${slug}`)}
          className="mb-3 flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Agent
        </button>

        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-[var(--color-text)]">
              <span className="text-[var(--color-text-muted)]">{slug}</span>
              <span className="mx-1 text-[var(--color-text-muted)]">/</span>
              {strategy.name}
            </h1>
            {strategy.description && (
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">{strategy.description}</p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowStrategyModal(true)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              title="Playbook & Learnings"
            >
              <FileText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Playbook</span>
            </button>
            <button
              onClick={() => setShowRoutinesBrowser(true)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              title="Routines & Reports"
            >
              <ScrollText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Routines</span>
            </button>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              disabled={strategy.status === "running"}
              className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-30"
              title={strategy.status === "running" ? "Stop strategy before deleting" : "Delete strategy"}
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Delete</span>
            </button>
            <AgentControls
              slug={slug!}
              sslug={sslug!}
              status={strategy.status}
              defaultContext={strategy.default_trading_context || (strategy.config.trading_context as string) || ""}
              agentConfig={strategy.config}
            />
          </div>
        </div>
      </div>

      {/* Meta strip */}
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
          {strategy.sessions.length} session{strategy.sessions.length !== 1 ? "s" : ""}
        </span>
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
              height={300}
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
      <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <PerformancePanel
          slug={slug!}
          sslug={sslug!}
          onSessionClick={handleSessionClick}
        />
      </div>

      {/* Playbook & Learnings Modal (near full-screen) */}
      {showStrategyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setShowStrategyModal(false)}
          />
          {/* Modal panel */}
          <div className="relative z-10 flex h-[90vh] w-[95vw] max-w-7xl flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
            {/* Modal header */}
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">
                Playbook & Learnings — {strategy.name}
              </h3>
              <button
                onClick={() => setShowStrategyModal(false)}
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
                  onSave={(value) => api.updateStrategyMd(slug!, sslug!, value)}
                  invalidateKey={["strategy", slug, sslug]}
                />
                <MarkdownEditor
                  label="Learnings"
                  sublabel="persists across sessions"
                  content={strategy.learnings}
                  onSave={(value) => api.updateStrategyLearnings(slug!, sslug!, value)}
                  invalidateKey={["strategy", slug, sslug]}
                />
              </div>
            </div>
          </div>
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

      {/* Delete Confirmation Dialog */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowDeleteConfirm(false)}>
          <div
            className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-2 text-lg font-semibold text-[var(--color-text)]">Delete Strategy</h2>
            <p className="mb-6 text-sm text-[var(--color-text-muted)]">
              Delete <strong className="text-[var(--color-text)]">{strategy.name}</strong>? This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                Cancel
              </button>
              <button
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition-opacity hover:bg-red-600 disabled:opacity-40"
              >
                {deleteMutation.isPending ? "Deleting..." : "Delete"}
              </button>
            </div>
            {deleteMutation.isError && (
              <p className="mt-3 text-xs text-red-400">Failed to delete strategy. It may be running.</p>
            )}
          </div>
        </div>
      )}

      {/* Session Reviewer Overlay */}
      {reviewerOpen && (strategy.sessions.length > 0 || strategy.experiments.length > 0) && (
        <SessionReviewer
          slug={slug!}
          sslug={sslug!}
          agentName={`${slug} / ${strategy.name}`}
          sessions={strategy.sessions}
          experiments={strategy.experiments}
          initialSessionNum={resolvedReviewerSession}
          initialKind={reviewerKind}
          serverName={serverName}
          controllerIds={controllerIds}
          onClose={() => setReviewerSessionNum(null)}
        />
      )}
    </div>
  );
}
