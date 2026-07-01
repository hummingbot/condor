import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
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
} from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useNavigate, useParams } from "react-router-dom";

import { MarkdownEditor } from "@/components/agent/AgentOverviewTab";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { type StrategySummary, api } from "@/lib/api";

const STATUS_STYLES: Record<string, { dot: string; bg: string; label: string }> = {
  running: { dot: "bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]", bg: "border-emerald-500/30 bg-emerald-500/5", label: "LIVE" },
  paused: { dot: "bg-amber-400", bg: "border-amber-500/30 bg-amber-500/5", label: "PAUSED" },
  stopped: { dot: "bg-red-400/60", bg: "border-red-500/20 bg-red-500/5", label: "STOPPED" },
  idle: { dot: "bg-[var(--color-text-muted)]/40", bg: "border-[var(--color-border)] bg-[var(--color-surface)]", label: "IDLE" },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.idle;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest ${s.bg} border`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

// ── Strategy Card ──

function StrategyCard({
  agentSlug,
  strategy,
  onDelete,
}: {
  agentSlug: string;
  strategy: StrategySummary;
  onDelete: () => void;
}) {
  const navigate = useNavigate();
  const totalPnl = strategy.total_pnl ?? 0;
  const totalPnlColor = totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const dayPnl = strategy.daily_pnl ?? 0;
  const dayPnlColor = dayPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const isLive = strategy.status === "running";

  return (
    <button
      onClick={() => navigate(`/agents/${agentSlug}/strategies/${strategy.slug}`)}
      className={`group relative w-full rounded-lg border text-left transition-all duration-200 hover:border-[var(--color-primary)]/40 hover:shadow-lg ${
        isLive
          ? "border-emerald-500/20 bg-emerald-500/[0.03]"
          : "border-[var(--color-border)] bg-[var(--color-surface)]"
      }`}
    >
      <div className="p-4">
        <div className="mb-3 flex items-start justify-between">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">{strategy.name}</h3>
          <div className="flex items-center gap-2">
            <StatusBadge status={strategy.status} />
            {!isLive && (
              <div
                className="opacity-0 transition-opacity group-hover:opacity-100"
                onClick={(e) => { e.stopPropagation(); onDelete(); }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); onDelete(); } }}
                role="button"
                tabIndex={0}
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-md border border-red-500/30 bg-red-500/10 text-red-400 transition-colors hover:bg-red-500/20">
                  <Trash2 className="h-3.5 w-3.5" />
                </span>
              </div>
            )}
          </div>
        </div>

        {strategy.description && (
          <p className="mb-3 text-xs text-[var(--color-text-muted)] line-clamp-2">
            {strategy.description}
          </p>
        )}

        <div className="grid grid-cols-4 gap-2 border-t border-[var(--color-border)]/50 pt-3">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Total PnL</span>
            <span className={`text-sm font-mono font-semibold ${totalPnlColor}`}>
              ${totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Session</span>
            <span className={`text-sm font-mono ${dayPnlColor}`}>
              ${dayPnl >= 0 ? "+" : ""}{dayPnl.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Open</span>
            <span className="text-sm font-mono text-[var(--color-text)]">{strategy.open_positions ?? 0}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Sessions</span>
            <span className="text-sm font-mono text-[var(--color-text)]">{strategy.session_count}</span>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end border-t border-[var(--color-border)]/30 px-4 py-2 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100">
        <span className="text-[11px]">Open</span>
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
        <h2 className="mb-4 text-lg font-semibold text-[var(--color-text)]">New Strategy</h2>

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
              placeholder="What does this strategy do?"
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

// ── Agent Detail Page ──

export function AgentDetail() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [showBrainModal, setShowBrainModal] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteStrategy, setDeleteStrategy] = useState<StrategySummary | null>(null);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);

  // Routine instances for ReportBrowser (routines live at the agent level,
  // shared across all of this agent's strategies)
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

  const deleteStrategyMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug!, deleteStrategy!.slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      setDeleteStrategy(null);
    },
  });

  // Close brain modal on Escape
  useEffect(() => {
    if (!showBrainModal) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setShowBrainModal(false); e.preventDefault(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [showBrainModal]);

  const { data: agent, isLoading } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug!),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  if (isLoading || !agent) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const strategies = agent.strategies || [];
  const running = strategies.filter((s) => s.status === "running");
  const isRunning = running.length > 0;

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
              <h1 className="text-2xl font-bold text-[var(--color-text)]">{agent.name}</h1>
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
              title="Agent brain (AGENT.md)"
            >
              <FileText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Brain</span>
            </button>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              disabled={isRunning}
              className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-30"
              title={isRunning ? "Stop all strategies before deleting" : "Delete agent"}
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Delete</span>
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-all hover:shadow-lg hover:shadow-[var(--color-primary)]/20"
            >
              <Plus className="h-4 w-4" />
              New Strategy
            </button>
          </div>
        </div>
      </div>

      {/* Consult panel (consultable agents) */}
      {agent.consultable && (
        <div className="mb-6">
          <ConsultPanel slug={agent.slug} whenToConsult={agent.when_to_consult} />
        </div>
      )}

      {/* Strategies */}
      <div>
        <h2 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <CircleDot className="h-3 w-3" />
          Strategies ({strategies.length})
        </h2>
        {strategies.length === 0 ? (
          <div className="flex h-56 flex-col items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/50">
            <CircleDot className="mb-3 h-9 w-9 text-[var(--color-text-muted)]/30" />
            <p className="mb-1 text-sm font-medium text-[var(--color-text)]">No strategies yet</p>
            <p className="mb-4 text-xs text-[var(--color-text-muted)]">
              {agent.consultable
                ? "This agent is consult-only. Add a strategy to make it loop."
                : "Add a playbook strategy for this agent to loop."}
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
            {strategies.map((strategy) => (
              <StrategyCard
                key={strategy.slug}
                agentSlug={agent.slug}
                strategy={strategy}
                onDelete={() => setDeleteStrategy(strategy)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Routines ReportBrowser (full-screen overlay, filtered to this agent) */}
      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      {/* Brain Modal (AGENT.md) */}
      {showBrainModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/60" onClick={() => setShowBrainModal(false)} />
          <div className="relative z-10 flex h-[90vh] w-[95vw] max-w-5xl flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">
                Agent Brain — {agent.name}
              </h3>
              <button
                onClick={() => setShowBrainModal(false)}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              <MarkdownEditor
                label="Brain"
                sublabel="AGENT.md — identity & domain knowledge"
                content={agent.agent_md}
                onSave={(value) => api.updateAgentMd(agent.slug, value)}
                invalidateKey={["agent", slug]}
              />
            </div>
          </div>
        </div>
      )}

      {/* Create Strategy Dialog */}
      <CreateStrategyDialog
        agentSlug={agent.slug}
        open={showCreate}
        onClose={() => setShowCreate(false)}
      />

      {/* Delete Agent Confirmation */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowDeleteConfirm(false)}>
          <div
            className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-2 text-lg font-semibold text-[var(--color-text)]">Delete Agent</h2>
            <p className="mb-6 text-sm text-[var(--color-text-muted)]">
              Delete <strong className="text-[var(--color-text)]">{agent.name}</strong> and all its strategies? This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                Cancel
              </button>
              <button
                onClick={() => deleteAgentMutation.mutate()}
                disabled={deleteAgentMutation.isPending}
                className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition-opacity hover:bg-red-600 disabled:opacity-40"
              >
                {deleteAgentMutation.isPending ? "Deleting..." : "Delete"}
              </button>
            </div>
            {deleteAgentMutation.isError && (
              <p className="mt-3 text-xs text-red-400">Failed to delete agent. It may have running strategies.</p>
            )}
          </div>
        </div>
      )}

      {/* Delete Strategy Confirmation */}
      {deleteStrategy && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setDeleteStrategy(null)}>
          <div
            className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-2 text-lg font-semibold text-[var(--color-text)]">Delete Strategy</h2>
            <p className="mb-6 text-sm text-[var(--color-text-muted)]">
              Delete <strong className="text-[var(--color-text)]">{deleteStrategy.name}</strong>? This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteStrategy(null)}
                className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                Cancel
              </button>
              <button
                onClick={() => deleteStrategyMutation.mutate()}
                disabled={deleteStrategyMutation.isPending}
                className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition-opacity hover:bg-red-600 disabled:opacity-40"
              >
                {deleteStrategyMutation.isPending ? "Deleting..." : "Delete"}
              </button>
            </div>
            {deleteStrategyMutation.isError && (
              <p className="mt-3 text-xs text-red-400">Failed to delete strategy. It may be running.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
