import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  Check,
  ChevronRight,
  CircleDot,
  Loader2,
  Pause,
  Plus,
  Radio,
  Square,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useNavigate } from "react-router-dom";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { ModeBadge } from "@/components/agent/ModeBadge";
import { StatusBadge } from "@/components/agent/StatusBadge";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import {
  type AgentSummary,
  type Delegation,
  type RunningInstance,
  api,
} from "@/lib/api";
import { formatCurrencyPnl, formatCurrencyVolume } from "@/lib/formatters";

function AgentCard({ agent, onClick, onDelete }: { agent: AgentSummary; onClick: () => void; onDelete: () => void }) {
  const totalPnl = agent.total_pnl ?? 0;
  const totalPnlColor = totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const dayPnl = agent.daily_pnl ?? 0;
  const dayPnlColor = dayPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const status = deriveAgentStatus(agent);
  const isLive = status === "running";

  return (
    <button
      onClick={onClick}
      className={`group relative w-full rounded-lg border text-left transition-all duration-200 hover:border-[var(--color-primary)]/40 hover:shadow-lg ${
        isLive
          ? "border-emerald-500/20 bg-emerald-500/[0.03]"
          : "border-[var(--color-border)] bg-[var(--color-surface)]"
      }`}
    >
      <div className="p-4">
        <div className="mb-3 flex items-start justify-between">
          <div className="flex items-center gap-2">
            <div className={`flex h-8 w-8 items-center justify-center rounded-md ${
              isLive ? "bg-emerald-500/10 text-emerald-400" : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
            }`}>
              <Brain className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-[var(--color-text)]">{agent.name}</h3>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={status} />
            {agent.status !== "running" && (
              <div
                aria-label="Delete agent"
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
            )}
          </div>
        </div>

        {agent.description && (
          <p className="mb-3 text-xs text-[var(--color-text-muted)] line-clamp-2">
            {agent.description}
          </p>
        )}

        <div className="grid grid-cols-4 gap-2 border-t border-[var(--color-border)]/50 pt-3">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Total PnL</span>
            <span className={`text-sm font-mono font-semibold ${totalPnlColor}`}>
              {formatCurrencyPnl(totalPnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Session</span>
            <span className={`text-sm font-mono ${dayPnlColor}`}>
              {formatCurrencyPnl(dayPnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Open</span>
            <span className="text-sm font-mono text-[var(--color-text)]">{agent.open_positions ?? 0}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Sessions</span>
            <span className="text-sm font-mono text-[var(--color-text)]">{agent.session_count}</span>
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

function CreateAgentDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  useEscapeKey(open, onClose);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [serverName, setServerName] = useState("");

  const { data: servers } = useQuery({
    queryKey: ["servers"],
    queryFn: api.getServers,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.createAgent({
        name,
        description,
        instructions,
        server_name: serverName || undefined,
      }),
    onSuccess: (agent) => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      onClose();
      setName("");
      setDescription("");
      setInstructions("");
      setServerName("");
      navigate(`/agents/${agent.slug}`);
    },
  });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold text-[var(--color-text)]">New Trading Agent</h2>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. BTC Grid Scalper"
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
              placeholder="What does this agent do?"
              rows={2}
              className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Instructions
            </label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="e.g. Domain knowledge and identity for this agent's brain — what it specializes in, how it reasons about markets..."
              rows={3}
              className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
            />
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              The agent's brain (AGENT.md). Add strategies afterward to make it loop.
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Server
            </label>
            <select
              value={serverName}
              onChange={(e) => setServerName(e.target.value)}
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]"
            >
              <option value="">Follow active chat server</option>
              {servers?.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              Pin this agent to a specific Hummingbot API server. Leave as “Follow
              active chat server” to use whichever server the chat is on.
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
            {createMutation.isPending ? "Creating..." : "Create Agent"}
          </button>
        </div>
        {createMutation.isError && (
          <p className="mt-3 text-xs text-red-400">
            Failed to create agent{createMutation.error instanceof Error ? `: ${createMutation.error.message}` : "."}
          </p>
        )}
      </div>
    </div>
  );
}

interface ActiveSession extends RunningInstance {
  agentName: string;
  agentSlug: string;
}

function ActiveSessionsTable({ sessions }: { sessions: ActiveSession[] }) {
  return (
    <div className="mb-6 rounded-lg border border-emerald-500/20 bg-[var(--color-surface)] p-4">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-emerald-400">
        <Zap className="h-3.5 w-3.5" /> Active Trading Sessions ({sessions.length})
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              <th className="px-2 py-1.5">Agent</th>
              <th className="px-2 py-1.5">Session</th>
              <th className="px-2 py-1.5">Trading Context</th>
              <th className="px-2 py-1.5 text-right">Realized PnL</th>
              <th className="px-2 py-1.5 text-right">Unrealized PnL</th>
              <th className="px-2 py-1.5 text-right">Volume</th>
              <th className="px-2 py-1.5 text-right">Open</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => {
              const realizedColor = s.realized_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
              const unrealizedColor = s.unrealized_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
              return (
                <tr key={s.agent_id} className="border-t border-[var(--color-border)]/40 font-mono">
                  <td className="px-2 py-2">
                    <Link
                      to={`/agents/${s.agentSlug}`}
                      className="font-medium text-[var(--color-primary)] hover:underline"
                    >
                      {s.agentName}
                    </Link>
                  </td>
                  <td className="px-2 py-2 text-[var(--color-text)]">
                    <span className="flex items-center gap-1.5">
                      #{s.session_num}
                      <ModeBadge mode={s.execution_mode} />
                    </span>
                  </td>
                  <td className="max-w-[200px] px-2 py-2 text-[var(--color-text-muted)]">
                    <span className="line-clamp-1">{s.trading_context || "—"}</span>
                  </td>
                  <td className={`px-2 py-2 text-right ${realizedColor}`}>
                    {formatCurrencyPnl(s.realized_pnl)}
                  </td>
                  <td className={`px-2 py-2 text-right ${unrealizedColor}`}>
                    {formatCurrencyPnl(s.unrealized_pnl)}
                  </td>
                  <td className="px-2 py-2 text-right text-[var(--color-text)]">
                    {formatCurrencyVolume(s.volume)}
                  </td>
                  <td className="px-2 py-2 text-right text-[var(--color-text)]">{s.open_count}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const DELEGATION_STATUS: Record<
  Delegation["status"],
  { dot: string; text: string; label: string }
> = {
  running: { dot: "bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]", text: "text-emerald-400", label: "RUNNING" },
  done: { dot: "bg-sky-400", text: "text-sky-400", label: "DONE" },
  error: { dot: "bg-red-400", text: "text-red-400", label: "ERROR" },
  stopped: { dot: "bg-[var(--color-text-muted)]/50", text: "text-[var(--color-text-muted)]", label: "STOPPED" },
};

function BackgroundTasks() {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirmStopId, setConfirmStopId] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["delegations"],
    queryFn: api.getDelegations,
    refetchInterval: 5000,
  });

  const stopMutation = useMutation({
    mutationFn: (taskId: string) => api.stopDelegation(taskId),
    onSuccess: () => {
      setConfirmStopId(null);
      queryClient.invalidateQueries({ queryKey: ["delegations"] });
    },
  });

  const delegations = data?.delegations ?? [];
  if (delegations.length === 0) return null;

  // Running first, then the rest in insertion order.
  const ordered = [...delegations].sort(
    (a, b) => (a.status === "running" ? 0 : 1) - (b.status === "running" ? 0 : 1),
  );
  const runningCount = delegations.filter((d) => d.status === "running").length;

  return (
    <div className="mb-6 rounded-lg border border-sky-500/20 bg-[var(--color-surface)] p-4">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-sky-400">
        <Radio className="h-3.5 w-3.5" /> Background Tasks ({delegations.length})
        {runningCount > 0 && (
          <span className="text-emerald-400">· {runningCount} running</span>
        )}
      </h3>
      <div className="space-y-2">
        {ordered.map((d) => {
          const s = DELEGATION_STATUS[d.status];
          const isOpen = expanded === d.task_id;
          const body = (d.status === "error" ? d.error : d.result)?.trim();
          return (
            <div
              key={d.task_id}
              className="rounded-md border border-[var(--color-border)]/60 bg-[var(--color-bg)]/40"
            >
              <div className="flex items-center gap-3 px-3 py-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${s.dot}`} />
                <Link
                  to={`/agents/${encodeURIComponent(d.agent)}`}
                  className="shrink-0 font-mono text-xs font-medium text-[var(--color-primary)] hover:underline"
                >
                  {d.agent}
                </Link>
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : d.task_id)}
                  className="min-w-0 flex-1 truncate text-left text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  title={d.task}
                >
                  {d.task.split("\n")[0] || "—"}
                </button>
                <span className={`shrink-0 text-[10px] font-bold uppercase tracking-wider ${s.text}`}>
                  {s.label}
                </span>
                {d.status === "running" &&
                  (confirmStopId === d.task_id ? (
                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        type="button"
                        onClick={() => stopMutation.mutate(d.task_id)}
                        disabled={stopMutation.isPending}
                        className="flex h-6 w-6 items-center justify-center rounded border border-red-500/30 bg-red-500/10 text-red-400 transition-colors hover:bg-red-500/20 disabled:opacity-40"
                        title="Confirm stop"
                      >
                        {stopMutation.isPending ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Check className="h-3 w-3" />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmStopId(null)}
                        className="flex h-6 w-6 items-center justify-center rounded border border-[var(--color-border)]/60 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
                        title="Cancel stop"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmStopId(d.task_id)}
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded border border-red-500/30 bg-red-500/10 text-red-400 transition-colors hover:bg-red-500/20"
                      title="Stop task"
                    >
                      <Square className="h-3 w-3" />
                    </button>
                  ))}
              </div>
              {isOpen && (
                <div className="border-t border-[var(--color-border)]/40 px-3 py-2">
                  <p className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                    {d.status === "error" ? "Error" : "Result"}
                  </p>
                  {d.status === "error" ? (
                    // Errors are raw (stack traces / plain messages) — keep monospace.
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-red-300">
                      {body || "(no output)"}
                    </pre>
                  ) : body ? (
                    // A delegation result is the agent's narrative final answer — render markdown.
                    <div className="chat-markdown max-h-64 overflow-auto text-xs text-[var(--color-text)]">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
                    </div>
                  ) : (
                    <p className="text-xs text-[var(--color-text-muted)]">
                      {d.status === "running" ? "Running…" : "(no output)"}
                    </p>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DeleteAgentDialog({
  agent,
  onClose,
}: {
  agent: AgentSummary | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteAgent(agent!.slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      onClose();
    },
  });

  return (
    <ConfirmDialog
      open={!!agent}
      title="Delete Agent"
      isPending={deleteMutation.isPending}
      isError={deleteMutation.isError}
      errorText="Failed to delete agent. It may be running."
      onConfirm={() => deleteMutation.mutate()}
      onClose={onClose}
    >
      Delete <strong className="text-[var(--color-text)]">{agent?.name}</strong>? This cannot be undone.
    </ConfirmDialog>
  );
}

export function Agents() {
  const navigate = useNavigate();
  const [showCreate, setShowCreate] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AgentSummary | null>(null);

  const { data: agents = [], isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
    refetchInterval: 10000,
  });

  const running = agents.filter((a) => deriveAgentStatus(a) === "running");
  const others = agents.filter((a) => deriveAgentStatus(a) !== "running");

  const activeSessions = useMemo<ActiveSession[]>(
    () =>
      agents.flatMap((a) =>
        (a.instances || []).map((inst) => ({
          ...inst,
          agentName: a.name,
          agentSlug: a.slug,
        })),
      ),
    [agents],
  );

  const aggTotalPnl = agents.reduce((sum, a) => sum + (a.total_pnl ?? 0), 0);
  const aggVolume = agents.reduce((sum, a) => sum + (a.total_volume ?? 0), 0);
  const aggOpen = agents.reduce((sum, a) => sum + (a.open_positions ?? 0), 0);
  const aggTotalColor = aggTotalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--color-text)]">Trading Agents</h1>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            {agents.length} agent{agents.length !== 1 ? "s" : ""}
            {running.length > 0 && (
              <span className="ml-2 text-emerald-400">
                <Zap className="mr-0.5 inline h-3 w-3" />
                {running.length} live
              </span>
            )}
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-all hover:shadow-lg hover:shadow-[var(--color-primary)]/20"
        >
          <Plus className="h-4 w-4" />
          New Agent
        </button>
      </div>

      {/* Aggregate strip */}
      {!isLoading && agents.length > 0 && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Total PnL</span>
            <span className={`text-lg font-mono font-semibold ${aggTotalColor}`}>
              {formatCurrencyPnl(aggTotalPnl)}
            </span>
          </div>
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Volume</span>
            <span className="text-lg font-mono text-[var(--color-text)]">
              {formatCurrencyVolume(aggVolume)}
            </span>
          </div>
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Open Positions</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{aggOpen}</span>
          </div>
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Live Agents</span>
            <span className="text-lg font-mono text-emerald-400">{running.length} / {agents.length}</span>
          </div>
        </div>
      )}

      {/* Active Sessions Table */}
      {!isLoading && activeSessions.length > 0 && (
        <ActiveSessionsTable sessions={activeSessions} />
      )}

      {/* Background Tasks (delegations) */}
      <BackgroundTasks />

      {isLoading ? (
        <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
        </div>
      ) : agents.length === 0 ? (
        <div className="flex h-64 flex-col items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/50">
          <Brain className="mb-3 h-10 w-10 text-[var(--color-text-muted)]/30" />
          <p className="mb-1 text-sm font-medium text-[var(--color-text)]">No agents yet</p>
          <p className="mb-4 text-xs text-[var(--color-text-muted)]">
            Create your first trading agent to get started
          </p>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white"
          >
            <Plus className="h-4 w-4" />
            Create Agent
          </button>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Live agents section */}
          {running.length > 0 && (
            <div>
              <h2 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-emerald-400">
                <CircleDot className="h-3 w-3" />
                Live
              </h2>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                {running.map((agent) => (
                  <AgentCard
                    key={agent.slug}
                    agent={agent}
                    onClick={() => navigate(`/agents/${agent.slug}`)}
                    onDelete={() => setDeleteTarget(agent)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Other agents */}
          {others.length > 0 && (
            <div>
              {running.length > 0 && (
                <h2 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                  <Pause className="h-3 w-3" />
                  All Agents
                </h2>
              )}
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                {others.map((agent) => (
                  <AgentCard
                    key={agent.slug}
                    agent={agent}
                    onClick={() => navigate(`/agents/${agent.slug}`)}
                    onDelete={() => setDeleteTarget(agent)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <CreateAgentDialog open={showCreate} onClose={() => setShowCreate(false)} />
      <DeleteAgentDialog agent={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </div>
  );
}
