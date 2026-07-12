import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  Brain,
  ChevronRight,
  CircleDot,
  FileText,
  MessageSquareText,
  Plus,
  ScrollText,
  Send,
  Server,
  Trash2,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useNavigate, useParams } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import { AgentMarketStrip } from "@/components/agent/AgentMarketStrip";
import {
  InstanceCard,
  MarkdownEditor,
  PerformancePanel,
} from "@/components/agent/AgentOverviewTab";
import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { SessionReviewer } from "@/components/agent/SessionReviewer";
import { DiscardChangesDialog } from "@/components/editor/EditorDialogs";
import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { type PlaybookSummary, type SessionInfo, api } from "@/lib/api";
import { formatDateTime } from "@/lib/formatters";
import { groupExecutorsByMarket } from "@/lib/executor-overlays";

// ── Playbook Card ──
// A strategy is a pure playbook template; its history lives on the agent
// (filter the sessions/performance below by this playbook's slug).

function PlaybookCard({
  agentSlug,
  playbook,
  onDelete,
}: {
  agentSlug: string;
  playbook: PlaybookSummary;
  onDelete: () => void;
}) {
  const navigate = useNavigate();

  return (
    <button
      onClick={() => navigate(`/agents/${agentSlug}/strategies/${playbook.slug}`)}
      className="group relative w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-left transition-all duration-200 hover:border-[var(--color-primary)]/40 hover:shadow-lg"
    >
      <div className="p-4">
        <div className="mb-2 flex items-start justify-between">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text)]">
            <BookOpen className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
            {playbook.name}
          </h3>
          <div
            aria-label="Delete strategy"
            className="opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onDelete(); } }}
            role="button"
            tabIndex={0}
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-md border border-red-500/30 bg-red-500/10 text-red-400 transition-colors hover:bg-red-500/20">
              <Trash2 className="h-3.5 w-3.5" />
            </span>
          </div>
        </div>

        {playbook.description && (
          <p className="mb-2 text-xs text-[var(--color-text-muted)] line-clamp-2">
            {playbook.description}
          </p>
        )}

        <div className="flex items-center gap-3 border-t border-[var(--color-border)]/50 pt-2 text-[11px] text-[var(--color-text-muted)]">
          <span>{playbook.session_count} session{playbook.session_count !== 1 ? "s" : ""}</span>
          {playbook.agent_key && <span className="font-mono text-[var(--color-primary)]">{playbook.agent_key}</span>}
        </div>
      </div>

      <div className="flex items-center justify-end border-t border-[var(--color-border)]/30 px-4 py-1.5 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100">
        <span className="text-[11px]">Edit playbook</span>
        <ChevronRight className="h-3.5 w-3.5" />
      </div>
    </button>
  );
}

// ── Create Strategy Dialog ──

function CreateStrategyDialog({
  agentSlug,
  open,
  onClose,
}: {
  agentSlug: string;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  useEscapeKey(open, onClose);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [defaultContext, setDefaultContext] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      api.createStrategy(agentSlug, { name, description, default_trading_context: defaultContext }),
    onSuccess: (strategy) => {
      queryClient.invalidateQueries({ queryKey: ["agent", agentSlug] });
      onClose();
      setName("");
      setDescription("");
      setDefaultContext("");
      navigate(`/agents/${agentSlug}/strategies/${strategy.slug}`);
    },
  });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold text-[var(--color-text)]">New Strategy (Playbook)</h2>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. BRL Market Maker"
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
              autoFocus
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Description
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this playbook do?"
              rows={2}
              className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Default Trading Context
            </label>
            <textarea
              value={defaultContext}
              onChange={(e) => setDefaultContext(e.target.value)}
              placeholder="e.g. Provide liquidity on BRL pairs, tight spreads, rebalance hourly..."
              rows={3}
              className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
            />
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              Tactic that guides this playbook's tick decisions. Can be overridden per session.
            </p>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!name.trim() || createMutation.isPending}
            className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-40"
          >
            {createMutation.isPending ? "Creating..." : "Create Strategy"}
          </button>
        </div>
        {createMutation.isError && (
          <p className="mt-3 text-xs text-red-400">Failed to create strategy.</p>
        )}
      </div>
    </div>
  );
}

// ── Consult Panel ──

function ConsultPanel({ slug, whenToConsult }: { slug: string; whenToConsult: string }) {
  const [task, setTask] = useState("");
  const consultMutation = useMutation({
    mutationFn: () => api.consultAgent(slug, { task }),
    onSuccess: () => setTask(""),
  });

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h3 className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        <MessageSquareText className="h-3.5 w-3.5" /> Consult
      </h3>
      {whenToConsult && (
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">{whenToConsult}</p>
      )}
      <div className="flex gap-2">
        <input
          type="text"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && task.trim()) consultMutation.mutate(); }}
          placeholder="Ask this agent…"
          className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
        />
        <button
          onClick={() => consultMutation.mutate()}
          disabled={!task.trim() || consultMutation.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-40"
        >
          <Send className="h-3.5 w-3.5" />
          {consultMutation.isPending ? "…" : "Ask"}
        </button>
      </div>
      {consultMutation.data && !consultMutation.isPending && (
        <div className="chat-markdown mt-3 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3 text-sm text-[var(--color-text)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{consultMutation.data.answer}</ReactMarkdown>
        </div>
      )}
      {consultMutation.isError && (
        <p className="mt-2 text-xs text-red-400">Consult failed.</p>
      )}
    </div>
  );
}

// ── Transcript viewer (delegation / consult sessions) ──

function TranscriptModal({
  slug,
  session,
  onClose,
}: {
  slug: string;
  session: SessionInfo;
  onClose: () => void;
}) {
  useEscapeKey(true, onClose);
  const { data } = useQuery({
    queryKey: ["agent", slug, "session", session.number, "transcript"],
    queryFn: () => api.getSessionTranscript(slug, session.number),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative z-10 flex h-[90vh] w-[95vw] max-w-4xl flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
            <span className="rounded bg-purple-500/10 px-1.5 py-0.5 text-[10px] font-bold uppercase text-purple-400">
              {session.kind}
            </span>
            {slug}_{session.number}
            {session.status && (
              <span className={`text-xs ${session.status === "done" ? "text-emerald-400" : session.status === "error" ? "text-red-400" : "text-[var(--color-text-muted)]"}`}>
                {session.status}
              </span>
            )}
          </h3>
          <button
            onClick={onClose}
            className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="chat-markdown flex-1 overflow-y-auto p-6 text-sm text-[var(--color-text)]">
          {data ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {data.content || "(no transcript captured)"}
            </ReactMarkdown>
          ) : (
            <div className="flex h-32 items-center justify-center">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Background runs (delegations + consults) list ──

function BackgroundRunsPanel({
  sessions,
  onOpen,
}: {
  sessions: SessionInfo[];
  onOpen: (s: SessionInfo) => void;
}) {
  const rows = sessions.filter((s) => s.kind !== "tick_loop");
  if (rows.length === 0) return null;
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        <MessageSquareText className="h-3.5 w-3.5" /> Delegations & Consults ({rows.length})
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              <th className="px-2 py-1">#</th>
              <th className="px-2 py-1">Kind</th>
              <th className="px-2 py-1">Task</th>
              <th className="px-2 py-1">Status</th>
              <th className="px-2 py-1">Ended</th>
              <th className="px-2 py-1 w-6" />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr
                key={s.number}
                onClick={() => onOpen(s)}
                className="cursor-pointer border-t border-[var(--color-border)]/40 transition-colors hover:bg-[var(--color-surface-hover)]"
              >
                <td className="px-2 py-1.5 font-mono text-[var(--color-text)]">{s.number}</td>
                <td className="px-2 py-1.5">
                  <span className="rounded bg-purple-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase text-purple-400">
                    {s.kind}
                  </span>
                </td>
                <td className="max-w-md truncate px-2 py-1.5 text-[var(--color-text-muted)]">{s.task || "—"}</td>
                <td className={`px-2 py-1.5 ${s.status === "done" ? "text-emerald-400" : s.status === "error" ? "text-red-400" : s.status === "running" ? "text-amber-400" : "text-[var(--color-text-muted)]"}`}>
                  {s.status || "—"}
                </td>
                <td className="px-2 py-1.5 text-[var(--color-text-muted)]">
                  {s.ended_at ? formatDateTime(Date.parse(s.ended_at)) : "—"}
                </td>
                <td className="px-2 py-1.5 text-[var(--color-text-muted)]">
                  <ChevronRight className="h-3.5 w-3.5" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Agent Detail Page ──
// The operational hub (refactor-01b): identity + playbooks + ALL history —
// live instances, per-session performance with a playbook filter, tick
// sessions/experiments (SessionReviewer), delegation/consult transcripts, and
// the agent-level learnings.

export function AgentDetail() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [showBrainModal, setShowBrainModal] = useState(false);
  // Unsaved-edit guard for the Brain (AGENT.md) / Learnings editors (CORR-093)
  const [brainDirty, setBrainDirty] = useState(false);
  const [learningsDirty, setLearningsDirty] = useState(false);
  const [showBrainDiscardConfirm, setShowBrainDiscardConfirm] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deletePlaybook, setDeletePlaybook] = useState<PlaybookSummary | null>(null);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);
  const [strategyFilter, setStrategyFilter] = useState("");
  const [reviewerSessionNum, setReviewerSessionNum] = useState<number | null>(null);
  const [reviewerKind, setReviewerKind] = useState<"session" | "experiment">("session");
  const [transcriptSession, setTranscriptSession] = useState<SessionInfo | null>(null);

  // Routine instances for ReportBrowser (routines live at the agent level)
  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  const deleteAgentMutation = useMutation({
    mutationFn: () => api.deleteAgent(slug!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate("/agents");
    },
  });

  const deletePlaybookMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug!, deletePlaybook!.slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      setDeletePlaybook(null);
    },
  });

  // Close the brain modal, dropping any unsaved-edit guards.
  const closeBrainModal = () => {
    setShowBrainModal(false);
    setShowBrainDiscardConfirm(false);
    setBrainDirty(false);
    setLearningsDirty(false);
  };

  // Backdrop click, Escape and the X button all route through here: with
  // unsaved edits they ask for confirmation instead of silently discarding.
  const requestCloseBrainModal = () => {
    if (brainDirty || learningsDirty) {
      setShowBrainDiscardConfirm(true);
    } else {
      closeBrainModal();
    }
  };

  useEscapeKey(showBrainModal && !showBrainDiscardConfirm, requestCloseBrainModal);

  const { data: agent, isLoading, error } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug!),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  // Live executor streaming for running instances
  const instances = agent?.instances || [];
  const hasRunning = instances.length > 0;
  const serverName = instances[0]?.server_name || agent?.server_name || "";
  const controllerIds = useMemo(
    () => instances.map((inst) => inst.agent_id).filter(Boolean),
    [instances],
  );
  const { executors: liveExecutors } = useAgentExecutors(
    hasRunning && serverName ? serverName : null,
    controllerIds,
  );
  const chartGroups = useMemo(
    () => (serverName ? groupExecutorsByMarket(liveExecutors) : []),
    [liveExecutors, serverName],
  );

  const handleSessionClick = useCallback(
    (sessionNum: number, kind?: "session" | "experiment") => {
      setReviewerSessionNum(sessionNum);
      setReviewerKind(kind || "session");
    },
    [],
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
          <button
            onClick={() => navigate("/agents")}
            className="mt-4 inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
          </button>
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

  const strategies = agent.strategies || [];
  const status = deriveAgentStatus(agent);
  const isRunning = agent.status === "running";
  const tickSessions = (agent.sessions || []).filter((s) => s.kind === "tick_loop");
  const reviewerOpen = reviewerSessionNum !== null;
  const resolvedReviewerSession =
    reviewerSessionNum ?? (tickSessions.length > 0 ? tickSessions[0].number : 0);

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={() => navigate("/agents")}
          className="mb-3 flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
        </button>

        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]">
                <Brain className="h-4 w-4" />
              </div>
              <h1 className="text-xl font-bold text-[var(--color-text)]">{agent.name}</h1>
            </div>
            {agent.description && (
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">{agent.description}</p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
              <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
                {agent.slug}
              </span>
              {agent.agent_key && (
                <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono text-[var(--color-primary)]">
                  {agent.agent_key}
                </span>
              )}
              {agent.consultable && (
                <span className="rounded border border-blue-500/30 bg-blue-500/10 px-2.5 py-1 font-medium text-blue-400">
                  consultable
                </span>
              )}
              {agent.tools && agent.tools.length > 0 && (
                <span className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
                  <Wrench className="h-3 w-3" /> {agent.tools.length} tool{agent.tools.length !== 1 ? "s" : ""}
                </span>
              )}
              {agent.server_name && (
                <span
                  className="flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 font-mono text-emerald-400"
                  title="Pinned Hummingbot API server"
                >
                  <Server className="h-3 w-3" /> {agent.server_name}
                </span>
              )}
              {Object.keys(agent.risk_limits || {}).length > 0 && (
                <span
                  className="flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 font-mono text-amber-400"
                  title={`Risk baseline (governs unattended delegations): ${JSON.stringify(agent.risk_limits)}`}
                >
                  <Zap className="h-3 w-3" /> risk baseline
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowRoutinesBrowser(true)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              title="Routines & Reports"
            >
              <ScrollText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Routines</span>
            </button>
            <button
              onClick={() => setShowBrainModal(true)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              title="Agent brain (AGENT.md) & learnings"
            >
              <FileText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Brain</span>
            </button>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              disabled={isRunning}
              className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-30"
              title={isRunning ? "Stop all sessions before deleting" : "Delete agent"}
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Delete</span>
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
            >
              <Plus className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">New Strategy</span>
            </button>
            <AgentControls slug={agent.slug} strategies={strategies} status={status} />
          </div>
        </div>
      </div>

      {/* Consult panel (consultable agents) */}
      {agent.consultable && (
        <div className="mb-6">
          <ConsultPanel slug={agent.slug} whenToConsult={agent.when_to_consult} />
        </div>
      )}

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

      {/* Playbooks */}
      <div className="mb-6">
        <h2 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <CircleDot className="h-3 w-3" />
          Strategies ({strategies.length})
        </h2>
        {strategies.length === 0 ? (
          <div className="flex h-40 flex-col items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/50">
            <CircleDot className="mb-3 h-9 w-9 text-[var(--color-text-muted)]/30" />
            <p className="mb-1 text-sm font-medium text-[var(--color-text)]">No strategies yet</p>
            <p className="mb-4 text-xs text-[var(--color-text-muted)]">
              {agent.consultable
                ? "This agent is consult-only. Add a playbook to make it loop."
                : "Add a playbook for this agent to loop."}
            </p>
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white"
            >
              <Plus className="h-4 w-4" />
              New Strategy
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {strategies.map((playbook) => (
              <PlaybookCard
                key={playbook.slug}
                agentSlug={agent.slug}
                playbook={playbook}
                onDelete={() => setDeletePlaybook(playbook)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Performance & sessions (with playbook filter chips) */}
      {(tickSessions.length > 0 || agent.experiments.length > 0 || hasRunning) && (
        <div className="mb-6">
          {strategies.length > 1 && (
            <div className="mb-3 flex items-center gap-1.5">
              <button
                onClick={() => setStrategyFilter("")}
                className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
                  strategyFilter === ""
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                All
              </button>
              {strategies.map((s) => (
                <button
                  key={s.slug}
                  onClick={() => setStrategyFilter(s.slug)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
                    strategyFilter === s.slug
                      ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  {s.name}
                </button>
              ))}
            </div>
          )}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <PerformancePanel
              slug={agent.slug}
              strategy={strategyFilter}
              onSessionClick={handleSessionClick}
            />
          </div>
        </div>
      )}

      {/* Delegations & consults */}
      <div className="mb-6">
        <BackgroundRunsPanel
          sessions={agent.sessions || []}
          onOpen={setTranscriptSession}
        />
      </div>

      {/* Routines ReportBrowser (full-screen overlay, filtered to this agent) */}
      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      {/* Brain & Learnings Modal */}
      {showBrainModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60" onClick={requestCloseBrainModal} />
          <div className="relative z-10 flex h-[90vh] w-[95vw] max-w-7xl flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">
                Brain & Learnings — {agent.name}
              </h3>
              <button
                onClick={requestCloseBrainModal}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              <div className="grid h-full grid-cols-1 gap-6 lg:grid-cols-2">
                <MarkdownEditor
                  label="Brain"
                  sublabel="AGENT.md — identity & domain knowledge"
                  content={agent.agent_md}
                  onSave={(value) => api.updateAgentMd(agent.slug, value)}
                  invalidateKey={["agent", slug]}
                  onDirtyChange={setBrainDirty}
                />
                <MarkdownEditor
                  label="Learnings"
                  sublabel="agent-level — all playbooks, [strategy]-prefixed"
                  content={agent.learnings}
                  onSave={(value) => api.updateAgentLearnings(agent.slug, value)}
                  invalidateKey={["agent", slug]}
                  onDirtyChange={setLearningsDirty}
                />
              </div>
            </div>
          </div>
          {showBrainDiscardConfirm && (
            <DiscardChangesDialog
              fileName={
                brainDirty && learningsDirty
                  ? "AGENT.md & learnings"
                  : brainDirty
                    ? "AGENT.md"
                    : "learnings"
              }
              onDiscard={closeBrainModal}
              onClose={() => setShowBrainDiscardConfirm(false)}
            />
          )}
        </div>
      )}

      {/* Create Strategy Dialog */}
      <CreateStrategyDialog
        agentSlug={agent.slug}
        open={showCreate}
        onClose={() => setShowCreate(false)}
      />

      {/* Delete Agent Confirmation */}
      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Agent"
        isPending={deleteAgentMutation.isPending}
        isError={deleteAgentMutation.isError}
        errorText="Failed to delete agent. It may have running sessions."
        onConfirm={() => deleteAgentMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete <strong className="text-[var(--color-text)]">{agent.name}</strong>? Its history stays on disk; the identity is removed. This cannot be undone.
      </ConfirmDialog>

      {/* Delete Playbook Confirmation */}
      <ConfirmDialog
        open={!!deletePlaybook}
        title="Delete Strategy"
        isPending={deletePlaybookMutation.isPending}
        isError={deletePlaybookMutation.isError}
        errorText="Failed to delete strategy. It may have a running session."
        onConfirm={() => deletePlaybookMutation.mutate()}
        onClose={() => setDeletePlaybook(null)}
      >
        Delete playbook <strong className="text-[var(--color-text)]">{deletePlaybook?.name}</strong>? Its past sessions stay in the agent's history. This cannot be undone.
      </ConfirmDialog>

      {/* Session Reviewer Overlay (tick sessions + experiments) */}
      {reviewerOpen && (tickSessions.length > 0 || agent.experiments.length > 0) && (
        <SessionReviewer
          slug={agent.slug}
          agentName={agent.name}
          sessions={tickSessions}
          experiments={agent.experiments}
          initialSessionNum={resolvedReviewerSession}
          initialKind={reviewerKind}
          serverName={serverName}
          controllerIds={controllerIds}
          onClose={() => setReviewerSessionNum(null)}
        />
      )}

      {/* Delegation/consult transcript viewer */}
      {transcriptSession && (
        <TranscriptModal
          slug={agent.slug}
          session={transcriptSession}
          onClose={() => setTranscriptSession(null)}
        />
      )}
    </div>
  );
}
