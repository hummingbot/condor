import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Brain,
  ChevronRight,
  FileText,
  History,
  MessageSquareText,
  ScrollText,
  Send,
  Server,
  Trash2,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useNavigate, useParams } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import {
  InstanceCard,
  MarkdownEditor,
  PerformancePanel,
} from "@/components/agent/AgentOverviewTab";
import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { RunReviewer } from "@/components/agent/RunReviewer";
import { KindBadge, RUN_STATUS_COLORS } from "@/components/agent/runStyles";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { DiscardChangesDialog } from "@/components/ui/DiscardChangesDialog";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { type RunMeta, api } from "@/lib/api";
import { formatDateTime } from "@/lib/formatters";

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

// ── Runs history (RunStore: one JSONL event stream per run, §7.1) ──

const RUN_FILTERS = [
  { id: "", label: "All" },
  { id: "session", label: "Sessions" },
  { id: "experiment", label: "Experiments" },
  { id: "scheduled", label: "Scheduled" },
  { id: "delegation", label: "Delegations" },
  { id: "consult", label: "Consults" },
] as const;

function RunsPanel({
  runs,
  onOpen,
}: {
  runs: RunMeta[];
  onOpen: (runId: string) => void;
}) {
  const [kindFilter, setKindFilter] = useState<string>("");
  if (runs.length === 0) return null;

  const counts = new Map<string, number>();
  for (const r of runs) counts.set(r.kind, (counts.get(r.kind) ?? 0) + 1);
  const visible = kindFilter ? runs.filter((r) => r.kind === kindFilter) : runs;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <History className="h-3.5 w-3.5" /> Runs ({runs.length})
        </h3>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          {RUN_FILTERS.map(
            (f) =>
              (f.id === "" || (counts.get(f.id) ?? 0) > 0) && (
                <button
                  key={f.id}
                  onClick={() => setKindFilter(f.id)}
                  className={`rounded-md px-2 py-1 text-[10px] font-medium transition-colors ${
                    kindFilter === f.id
                      ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  }`}
                >
                  {f.label}
                  {f.id && ` (${counts.get(f.id)})`}
                </button>
              ),
          )}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              <th className="px-2 py-1">#</th>
              <th className="px-2 py-1">Kind</th>
              <th className="px-2 py-1">Task</th>
              <th className="px-2 py-1">Status</th>
              <th className="px-2 py-1">Model</th>
              <th className="px-2 py-1">Started</th>
              <th className="px-2 py-1">Ended</th>
              <th className="px-2 py-1 w-6" />
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr
                key={r.run_id}
                onClick={() => onOpen(r.run_id)}
                className="cursor-pointer border-t border-[var(--color-border)]/40 transition-colors hover:bg-[var(--color-surface-hover)]"
              >
                <td className="px-2 py-1.5 font-mono text-[var(--color-text)]">{r.display_seq}</td>
                <td className="px-2 py-1.5">
                  <KindBadge kind={r.kind} />
                </td>
                <td className="max-w-md truncate px-2 py-1.5 text-[var(--color-text-muted)]">
                  {r.task ? r.task.split("\n")[0] : "—"}
                </td>
                <td className={`px-2 py-1.5 font-medium ${RUN_STATUS_COLORS[r.status] ?? "text-[var(--color-text-muted)]"}`}>
                  {r.status || "—"}
                </td>
                <td className="px-2 py-1.5 font-mono text-[var(--color-text-muted)]">{r.model || "—"}</td>
                <td className="px-2 py-1.5 text-[var(--color-text-muted)]">
                  {r.started_at ? formatDateTime(r.started_at * 1000) : "—"}
                </td>
                <td className="px-2 py-1.5 text-[var(--color-text-muted)]">
                  {r.ended_at ? formatDateTime(r.ended_at * 1000) : "—"}
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
// The operational hub: identity + ALL history — live instances, per-run
// performance, the run history list (RunReviewer opens any run's event
// stream), and the agent-level learnings.

export function AgentDetail() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [showBrainModal, setShowBrainModal] = useState(false);
  // Unsaved-edit guard for the Brain (AGENT.md) / Learnings editors (CORR-093)
  const [brainDirty, setBrainDirty] = useState(false);
  const [learningsDirty, setLearningsDirty] = useState(false);
  const [showBrainDiscardConfirm, setShowBrainDiscardConfirm] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);
  const [reviewerRunId, setReviewerRunId] = useState<string | null>(null);

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

  const instances = agent?.instances || [];
  const hasRunning = instances.length > 0;

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

  const status = deriveAgentStatus(agent);
  const isRunning = agent.status === "running";
  const runs = agent.runs || [];

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
                  title="Pinned server"
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
            <AgentControls
              slug={agent.slug}
              status={status}
              defaultConfig={agent.default_config || {}}
              defaultTradingContext={agent.default_trading_context || ""}
            />
          </div>
        </div>
      </div>

      {/* Consult panel (consultable agents) */}
      {agent.consultable && (
        <div className="mb-6">
          <ConsultPanel slug={agent.slug} whenToConsult={agent.when_to_consult} />
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

      {/* Performance & runs */}
      {(runs.length > 0 || hasRunning) && (
        <div className="mb-6">
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <PerformancePanel
              slug={agent.slug}
              onRunClick={setReviewerRunId}
            />
          </div>
        </div>
      )}

      {/* Run history (all kinds, filterable) */}
      <div className="mb-6">
        <RunsPanel runs={runs} onOpen={setReviewerRunId} />
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
                  sublabel="agent-level — accumulated across sessions"
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

      {/* Run Reviewer Overlay (any run's event stream) */}
      {reviewerRunId && (
        <RunReviewer
          slug={agent.slug}
          agentName={agent.name}
          initialRunId={reviewerRunId}
          onClose={() => setReviewerRunId(null)}
        />
      )}
    </div>
  );
}
