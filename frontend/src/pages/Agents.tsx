import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  ChevronRight,
  CircleDot,
  Pause,
  Plus,
  Zap,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { type AgentSummary, api } from "@/lib/api";

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

function AgentCard({ agent, onClick }: { agent: AgentSummary; onClick: () => void }) {
  const totalPnl = agent.total_pnl ?? 0;
  const totalPnlColor = totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const dayPnl = agent.daily_pnl ?? 0;
  const dayPnlColor = dayPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const isLive = agent.status === "running";

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
          <StatusBadge status={agent.status} />
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
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [defaultContext, setDefaultContext] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      api.createAgent({ name, description, default_trading_context: defaultContext }),
    onSuccess: (agent) => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      onClose();
      setName("");
      setDescription("");
      setDefaultContext("");
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
              Default Trading Context
            </label>
            <textarea
              value={defaultContext}
              onChange={(e) => setDefaultContext(e.target.value)}
              placeholder="e.g. Trade meme coins aggressively, focus on momentum breakouts with tight stops..."
              rows={3}
              className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
            />
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              Natural language context that guides trading decisions. Can be overridden per session.
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
      </div>
    </div>
  );
}

export function Agents() {
  const navigate = useNavigate();
  const [showCreate, setShowCreate] = useState(false);

  const { data: agents = [], isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
    refetchInterval: 10000,
  });

  const running = agents.filter((a) => a.status === "running");
  const others = agents.filter((a) => a.status !== "running");

  const aggTotalPnl = agents.reduce((sum, a) => sum + (a.total_pnl ?? 0), 0);
  const aggVolume = agents.reduce((sum, a) => sum + (a.total_volume ?? 0), 0);
  const aggOpen = agents.reduce((sum, a) => sum + (a.open_positions ?? 0), 0);
  const aggTotalColor = aggTotalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";

  return (
    <div className="mx-auto w-full max-w-7xl">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">Trading Agents</h1>
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
              ${aggTotalPnl >= 0 ? "+" : ""}{aggTotalPnl.toFixed(2)}
            </span>
          </div>
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Volume</span>
            <span className="text-lg font-mono text-[var(--color-text)]">
              ${aggVolume.toLocaleString(undefined, { maximumFractionDigits: 0 })}
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
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <CreateAgentDialog open={showCreate} onClose={() => setShowCreate(false)} />
    </div>
  );
}
