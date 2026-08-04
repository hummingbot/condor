import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Brain,
  CircleDot,
  FileText,
  MessageSquareText,
  Plus,
  Repeat,
  ScrollText,
  Server,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { MarkdownEditor } from "@/components/agent/AgentOverviewTab";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { EntityCard } from "@/components/agent/EntityCard";
import { DiscardChangesDialog } from "@/components/editor/EditorDialogs";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { type StrategySummary, api } from "@/lib/api";

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

// ── Server pin ──

/**
 * Which server this Agent's tools trade on, wherever it runs.
 *
 * A pin beats the chat's ambient selection everywhere the Agent is used —
 * chatted, consulted or looped — so it is the Agent's decision, and this is the
 * page that owns it. Before this it could only be changed by hand-editing
 * AGENT.md front matter, which is why a locked chat chip links here.
 */
function ServerPinPicker({ slug, serverName }: { slug: string; serverName: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const { data: servers } = useQuery({
    queryKey: ["servers"],
    queryFn: api.getServers,
    enabled: open,
  });

  const pin = useMutation({
    mutationFn: (name: string) => api.updateAgentConfig(slug, { server_name: name }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent", slug] }),
  });

  const choose = (name: string) => {
    setOpen(false);
    if (name !== serverName) pin.mutate(name);
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={pin.isPending}
        title={
          serverName
            ? `Pinned to ${serverName} — every run uses this server`
            : "No pin: follows whichever server the chat is on"
        }
        className={`flex items-center gap-1 rounded border px-2.5 py-1 transition-colors disabled:opacity-50 ${
          serverName
            ? "border-emerald-500/30 bg-emerald-500/10 font-mono text-emerald-400 hover:border-emerald-500/60"
            : "border-dashed border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-text)]"
        }`}
      >
        <Server className="h-3 w-3" /> {serverName || "No server pin"}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-50 mt-1 min-w-[220px] rounded border border-[var(--color-border)] bg-[var(--color-surface)] py-0.5 shadow-lg">
            <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Pin to server
            </div>
            {(servers ?? []).map((s) => (
              <button
                key={s.name}
                onClick={() => choose(s.name)}
                className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                  s.name === serverName
                    ? "font-medium text-[var(--color-primary)]"
                    : "text-[var(--color-text)]"
                }`}
              >
                <CircleDot
                  className={`h-2.5 w-2.5 shrink-0 ${
                    s.online ? "text-[var(--color-green)]" : "text-[var(--color-text-muted)]"
                  }`}
                />
                <span className="truncate">{s.name}</span>
              </button>
            ))}
            <button
              onClick={() => choose("")}
              className={`mt-0.5 w-full border-t border-[var(--color-border)] px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
                serverName ? "text-[var(--color-text)]" : "font-medium text-[var(--color-primary)]"
              }`}
            >
              No pin — follow the chat's selection
            </button>
          </div>
        </>
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
  // Unsaved-edit guard for the Brain (AGENT.md) editor (CORR-093)
  const [brainDirty, setBrainDirty] = useState(false);
  const [showBrainDiscardConfirm, setShowBrainDiscardConfirm] = useState(false);
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

  // Every Agent is loopable. This brings its implicit default playbook on disk
  // so the normal strategy UI (config, start, sessions) can drive the loop.
  const createDefaultLoopMutation = useMutation({
    mutationFn: () => api.createDefaultStrategy(slug!),
    onSuccess: (strategy) => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      navigate(`/agents/${slug}/strategies/${strategy.slug}`);
    },
  });

  const deleteStrategyMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug!, deleteStrategy!.slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      setDeleteStrategy(null);
    },
  });

  // Close the brain modal, dropping any unsaved-edit guards.
  const closeBrainModal = () => {
    setShowBrainModal(false);
    setShowBrainDiscardConfirm(false);
    setBrainDirty(false);
  };

  // Backdrop click, Escape and the X button all route through here: with
  // unsaved edits they ask for confirmation instead of silently discarding.
  const requestCloseBrainModal = () => {
    if (brainDirty) {
      setShowBrainDiscardConfirm(true);
    } else {
      closeBrainModal();
    }
  };

  // Close modals on Escape (the discard dialog owns Escape while open;
  // the delete dialogs handle Escape internally via ConfirmDialog).
  useEscapeKey(showBrainModal && !showBrainDiscardConfirm, requestCloseBrainModal);

  const { data: agent, isLoading, error } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug!),
    enabled: !!slug,
    refetchInterval: 5000,
  });

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
              {agent.tools && agent.tools.length > 0 && (
                <span className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
                  <Wrench className="h-3 w-3" /> {agent.tools.length} tool{agent.tools.length !== 1 ? "s" : ""}
                </span>
              )}
              <ServerPinPicker slug={agent.slug} serverName={agent.server_name} />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate(`/agents?agent=${encodeURIComponent(agent.slug)}`)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              title={`Chat with ${agent.name}`}
            >
              <MessageSquareText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Chat</span>
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

      {/* Strategies */}
      <div>
        <h2 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <CircleDot className="h-3 w-3" />
          Strategies ({strategies.length})
        </h2>
        {strategies.length === 0 ? (
          <div className="flex h-56 flex-col items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/50">
            <CircleDot className="mb-3 h-9 w-9 text-[var(--color-text-muted)]/30" />
            <p className="mb-1 text-sm font-medium text-[var(--color-text)]">
              No strategies yet
            </p>
            <p className="mb-4 max-w-md text-center text-xs text-[var(--color-text-muted)]">
              This agent can already be consulted and delegated to. To let it run on a
              loop, start from its default playbook — its own brain drives each tick —
              or write a dedicated one.
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => createDefaultLoopMutation.mutate()}
                disabled={createDefaultLoopMutation.isPending}
                className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                <CircleDot className="h-4 w-4" />
                {createDefaultLoopMutation.isPending ? "Creating…" : "Use default loop"}
              </button>
              <button
                onClick={() => setShowCreate(true)}
                className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-sm font-medium text-[var(--color-text)]"
              >
                <Plus className="h-4 w-4" />
                New Strategy
              </button>
            </div>
            {createDefaultLoopMutation.isError && (
              <p className="mt-3 text-xs text-[var(--color-red)]">
                Could not create the default loop.
              </p>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {strategies.map((strategy) => (
              <EntityCard
                key={strategy.slug}
                entity={strategy}
                icon={Repeat}
                deleteLabel="Delete strategy"
                onClick={() => navigate(`/agents/${agent.slug}/strategies/${strategy.slug}`)}
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
          <div className="absolute inset-0 bg-black/60" onClick={requestCloseBrainModal} />
          <div className="relative z-10 flex h-[90vh] w-[95vw] max-w-5xl flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">
                Agent Brain — {agent.name}
              </h3>
              <button
                onClick={requestCloseBrainModal}
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
                onDirtyChange={setBrainDirty}
              />
            </div>
          </div>
          {/* Unsaved-changes confirmation before discarding edits */}
          {showBrainDiscardConfirm && (
            <DiscardChangesDialog
              fileName="AGENT.md"
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
        errorText="Failed to delete agent. It may have running strategies."
        onConfirm={() => deleteAgentMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete <strong className="text-[var(--color-text)]">{agent.name}</strong> and all its strategies? This cannot be undone.
      </ConfirmDialog>

      {/* Delete Strategy Confirmation */}
      <ConfirmDialog
        open={!!deleteStrategy}
        title="Delete Strategy"
        isPending={deleteStrategyMutation.isPending}
        isError={deleteStrategyMutation.isError}
        errorText="Failed to delete strategy. It may be running."
        onConfirm={() => deleteStrategyMutation.mutate()}
        onClose={() => setDeleteStrategy(null)}
      >
        Delete <strong className="text-[var(--color-text)]">{deleteStrategy?.name}</strong>? This cannot be undone.
      </ConfirmDialog>
    </div>
  );
}
