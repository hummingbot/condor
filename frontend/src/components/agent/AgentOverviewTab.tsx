import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Clock,
  Save,
  Zap,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { AgentMarketStrip } from "@/components/agent/AgentMarketStrip";
import { AgentPnlChart, sessionsToDataPoints } from "@/components/agent/AgentPnlChart";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { type AgentDetail, type ExecutorInfo, api } from "@/lib/api";

// ── Markdown Editor ──

function MarkdownEditor({
  slug,
  label,
  sublabel,
  content,
  mutationFn,
}: {
  slug: string;
  label: string;
  sublabel: string;
  content: string;
  mutationFn: (slug: string, value: string) => Promise<unknown>;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(content);
  const [dirty, setDirty] = useState(false);

  const saveMut = useMutation({
    mutationFn: () => mutationFn(slug, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      setDirty(false);
    },
  });

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    setDirty(true);
  }, []);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">{label}</span>
          <span className="ml-2 text-[10px] text-[var(--color-text-muted)]">{sublabel}</span>
        </div>
        <button
          onClick={() => saveMut.mutate()}
          disabled={!dirty || saveMut.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-all disabled:opacity-30"
        >
          <Save className="h-3.5 w-3.5" />
          {saveMut.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      <textarea
        value={value}
        onChange={handleChange}
        spellCheck={false}
        className="min-h-[500px] w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 font-mono text-sm leading-relaxed text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]/50"
      />
    </div>
  );
}

// ── Instance Card ──

export function InstanceCard({ instance }: { instance: import("@/lib/api").RunningInstance }) {
  const riskLimits = (instance.risk_limits || {}) as Record<string, unknown>;
  const statusColor = instance.status === "running" ? "text-emerald-400" : instance.status === "paused" ? "text-amber-400" : "text-[var(--color-text-muted)]";
  const mode = instance.execution_mode || "loop";
  const modeBadge = mode === "dry_run"
    ? { label: "DRY RUN", cls: "border-blue-500/30 bg-blue-500/10 text-blue-400" }
    : mode === "run_once"
      ? { label: "RUN ONCE", cls: "border-amber-500/30 bg-amber-500/10 text-amber-400" }
      : null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-bold text-[var(--color-text)]">{instance.agent_id}</span>
          <span className={`text-xs font-semibold uppercase ${statusColor}`}>{instance.status}</span>
          {modeBadge && (
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${modeBadge.cls}`}>
              {modeBadge.label}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
          <span>Ticks: {instance.tick_count}</span>
          <span className={instance.daily_pnl >= 0 ? "text-emerald-400" : "text-red-400"}>
            PnL: ${instance.daily_pnl.toFixed(2)}
          </span>
        </div>
      </div>

      {instance.trading_context && (
        <p className="mb-3 whitespace-pre-wrap rounded-md bg-[var(--color-surface)] p-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
          {instance.trading_context}
        </p>
      )}

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-4">
        {instance.agent_key && (
          <div className="flex justify-between">
            <span className="text-[var(--color-text-muted)]">model</span>
            <span className="text-[var(--color-primary)]">{instance.agent_key}</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">server</span>
          <span className="text-[var(--color-text)]">{instance.server_name || "auto"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">budget</span>
          <span className="text-[var(--color-text)]">${instance.total_amount_quote}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">frequency</span>
          <span className="text-[var(--color-text)]">{instance.frequency_sec}s</span>
        </div>
        {Object.entries(riskLimits).map(([k, v]) => (
          <div key={k} className="flex justify-between">
            <span className="text-[var(--color-text-muted)]">{k.replace("max_", "").replace(/_/g, " ")}</span>
            <span className="text-[var(--color-text)]">{String(v)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Performance Panel ──

export function PerformancePanel({ slug }: { slug: string }) {
  const { data } = useQuery({
    queryKey: ["agent-performance", slug],
    queryFn: () => api.getAgentPerformance(slug),
    refetchInterval: 10000,
  });
  const totals = data?.totals || {};
  const allRows = data?.sessions || [];
  const sessions = allRows.filter((s) => s.kind === "session");
  const totalPnl = Number(totals.total_pnl ?? 0);
  const realized = Number(totals.realized_pnl ?? 0);
  const unrealized = Number(totals.unrealized_pnl ?? 0);
  const volume = Number(totals.volume ?? 0);
  const fees = Number(totals.fees ?? 0);
  const openPos = Number(totals.open_positions ?? 0);
  const pnlColor = totalPnl >= 0 ? "text-emerald-400" : "text-red-400";

  const closed = sessions.reduce((s, x) => s + x.closed_count, 0);
  const wins = sessions.reduce((s, x) => s + Math.round(x.win_rate * x.closed_count), 0);
  const winRate = closed > 0 ? (wins / closed) * 100 : 0;
  const trades = sessions.reduce((s, x) => s + x.trade_count, 0);

  // PnL chart data from session-level performance
  const pnlData = useMemo(() => sessionsToDataPoints(sessions), [sessions]);

  return (
    <div className="space-y-4 lg:col-span-2">
      {/* Stat grid */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Zap className="h-3.5 w-3.5" /> Performance
        </h3>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-8">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Total PnL</span>
            <span className={`text-lg font-mono font-semibold ${pnlColor}`}>
              ${totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Realized</span>
            <span className="text-lg font-mono text-[var(--color-text)]">${realized.toFixed(2)}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Unrealized</span>
            <span className="text-lg font-mono text-[var(--color-text)]">${unrealized.toFixed(2)}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Volume</span>
            <span className="text-lg font-mono text-[var(--color-text)]">
              ${volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Fees</span>
            <span className="text-lg font-mono text-[var(--color-text)]">${fees.toFixed(2)}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Win Rate</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{winRate.toFixed(0)}%</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Trades</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{trades}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Open</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{openPos}</span>
          </div>
        </div>
      </div>

      {/* PnL equity curve */}
      {pnlData.length > 1 && (
        <AgentPnlChart data={pnlData} height={180} title="PnL Equity Curve" />
      )}

      {/* Sessions table */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Clock className="h-3.5 w-3.5" /> Sessions ({sessions.length})
        </h3>
        {sessions.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)]">No sessions yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                  <th className="px-2 py-1">#</th>
                  <th className="px-2 py-1">Kind</th>
                  <th className="px-2 py-1">Status</th>
                  <th className="px-2 py-1 text-right">Total PnL</th>
                  <th className="px-2 py-1 text-right">Realized</th>
                  <th className="px-2 py-1 text-right">Unrealized</th>
                  <th className="px-2 py-1 text-right">Volume</th>
                  <th className="px-2 py-1 text-right">Trades</th>
                  <th className="px-2 py-1 text-right">Open</th>
                </tr>
              </thead>
              <tbody>
                {sessions
                  .slice()
                  .sort((a, b) => (b.kind === a.kind ? b.session_num - a.session_num : a.kind === "experiment" ? 1 : -1))
                  .map((s) => {
                    const pnlCol = s.total_pnl >= 0 ? "text-emerald-400" : "text-red-400";
                    return (
                      <tr
                        key={s.agent_id}
                        className="border-t border-[var(--color-border)]/40 font-mono"
                      >
                        <td className="px-2 py-1.5 text-[var(--color-text)]">{s.session_num}</td>
                        <td className="px-2 py-1.5 text-[var(--color-text-muted)]">{s.kind}</td>
                        <td className={`px-2 py-1.5 ${s.status === "running" ? "text-emerald-400" : "text-[var(--color-text-muted)]"}`}>
                          {s.status || "—"}
                        </td>
                        <td className={`px-2 py-1.5 text-right ${pnlCol}`}>
                          ${s.total_pnl >= 0 ? "+" : ""}{s.total_pnl.toFixed(2)}
                        </td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">${s.realized_pnl.toFixed(2)}</td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">${s.unrealized_pnl.toFixed(2)}</td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">
                          ${s.volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        </td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">{s.trade_count}</td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">{s.open_count}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Overview Tab ──

export function OverviewTab({ agent }: { agent: AgentDetail }) {
  const config = agent.config as Record<string, unknown>;
  const instances = agent.instances || [];
  const hasRunning = instances.length > 0;
  const serverName = (config.server_name as string) || "";

  // Derive controller IDs from active instances for WS executor streaming
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

  return (
    <div className="space-y-6">
      {/* Agent Meta Strip */}
      <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <span className="rounded-full bg-[var(--color-surface)] px-2.5 py-1 border border-[var(--color-border)]">
          {agent.sessions.length} session{agent.sessions.length !== 1 ? "s" : ""}
        </span>
        <span className="rounded-full bg-[var(--color-surface)] px-2.5 py-1 border border-[var(--color-border)] font-mono">
          {agent.slug}
        </span>
        {agent.agent_id && (
          <span className="rounded-full bg-[var(--color-surface)] px-2.5 py-1 border border-[var(--color-border)] font-mono">
            {agent.agent_id}
          </span>
        )}
      </div>

      {/* Market Context Strip */}
      {hasRunning && liveExecutors.length > 0 && (
        <AgentMarketStrip serverName={serverName} executors={liveExecutors} />
      )}

      {/* Performance Panel + PnL Chart */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <PerformancePanel slug={agent.slug} />
      </div>

      {/* Live Executor Charts */}
      {hasRunning && chartGroups.length > 0 && (
        <div className="space-y-4">
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
        <div className="rounded-lg border border-emerald-500/20 bg-[var(--color-surface)] p-4">
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

      {/* Strategy + Learnings Editors */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
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
  );
}
