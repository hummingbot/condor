import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, FileText, ScrollText, X, Zap } from "lucide-react";
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
import { type ExecutorInfo, api } from "@/lib/api";

// ── Main Page ──

export function AgentDetail() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const location = useLocation();

  const [reviewerSessionNum, setReviewerSessionNum] = useState<number | null>(null);
  const [reviewerKind, setReviewerKind] = useState<"session" | "experiment">("session");
  const [showStrategyModal, setShowStrategyModal] = useState(false);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);

  // Check location.state for agent-switching (SessionReviewer up/down nav)
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

  const { data: agent, isLoading } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug!),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  // Routine instances for ReportBrowser
  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  // All agents list for up/down nav in reviewer
  const { data: allAgents } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
  });

  // Derive controller IDs from active instances for WS executor streaming
  const instances = agent?.instances || [];
  const hasRunning = instances.length > 0;
  const serverName = (agent?.config?.server_name as string) || "";

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
  const chartGroups = useMemo(() => {
    if (!serverName || liveExecutors.length === 0) return [];
    const groups = new Map<string, ExecutorInfo[]>();
    for (const ex of liveExecutors) {
      if (!ex.trading_pair) continue;
      const key = `${ex.connector}:${ex.trading_pair}`;
      const arr = groups.get(key);
      if (arr) arr.push(ex);
      else groups.set(key, [ex]);
    }
    return Array.from(groups.entries());
  }, [liveExecutors, serverName]);

  // Session/experiment click -> open reviewer
  const handleSessionClick = useCallback((sessionNum: number, kind?: "session" | "experiment") => {
    setReviewerSessionNum(sessionNum);
    setReviewerKind(kind || "session");
  }, []);

  // Agent switching from SessionReviewer
  const handleSwitchAgent = useCallback(
    (targetSlug: string, sessionNum?: number) => {
      navigate(`/agents/${targetSlug}`, {
        state: { openReviewer: true, sessionNum: sessionNum ?? null },
      });
    },
    [navigate],
  );

  if (isLoading || !agent) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const reviewerOpen = reviewerSessionNum !== null;
  const resolvedReviewerSession =
    reviewerSessionNum ?? (agent.sessions.length > 0 ? agent.sessions[0].number : 0);

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-4">
        <button
          onClick={() => navigate("/agents")}
          className="mb-3 flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
        </button>

        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-[var(--color-text)]">{agent.name}</h1>
            {agent.description && (
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">{agent.description}</p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowStrategyModal(true)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              title="Strategy & Learnings"
            >
              <FileText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Strategy</span>
            </button>
            <button
              onClick={() => setShowRoutinesBrowser(true)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              title="Routines & Reports"
            >
              <ScrollText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Routines</span>
            </button>
            <AgentControls
              slug={slug!}
              status={agent.status}
              defaultContext={agent.default_trading_context || (agent.config.trading_context as string) || ""}
              agentConfig={agent.config}
            />
          </div>
        </div>
      </div>

      {/* Meta strip */}
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
          {agent.sessions.length} session{agent.sessions.length !== 1 ? "s" : ""}
        </span>
        <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
          {agent.slug}
        </span>
        {agent.agent_id && (
          <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
            {agent.agent_id}
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
          slug={agent.slug}
          onSessionClick={handleSessionClick}
        />
      </div>

      {/* Strategy & Learnings Modal (near full-screen) */}
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
                Strategy & Learnings — {agent.name}
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
                  slug={agent.slug}
                  label="Strategy"
                  sublabel="agent.md"
                  content={agent.agent_md}
                  mutationFn={api.updateAgentMd}
                />
                <MarkdownEditor
                  slug={agent.slug}
                  label="Learnings"
                  sublabel="persists across sessions"
                  content={agent.learnings}
                  mutationFn={api.updateAgentLearnings}
                />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Routines ReportBrowser (full-screen overlay with agent filter) */}
      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={agent.slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      {/* Session Reviewer Overlay */}
      {reviewerOpen && (agent.sessions.length > 0 || agent.experiments.length > 0) && (
        <SessionReviewer
          slug={slug!}
          agentName={agent.name}
          sessions={agent.sessions}
          experiments={agent.experiments}
          initialSessionNum={resolvedReviewerSession}
          initialKind={reviewerKind}
          serverName={serverName}
          controllerIds={controllerIds}
          allAgents={allAgents}
          onClose={() => setReviewerSessionNum(null)}
          onSwitchAgent={handleSwitchAgent}
        />
      )}
    </div>
  );
}
