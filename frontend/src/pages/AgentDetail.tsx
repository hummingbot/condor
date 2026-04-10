import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Brain,
  Camera,
  ChevronDown,
  ChevronRight,
  Clock,
  LayoutList,
  Lightbulb,
  MessageSquareText,
  Pause,
  Play,
  Save,
  Server,
  Settings,
  Square,
  FlaskConical,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { type AgentDetail as AgentDetailType, type AgentExecutorRow, type ExperimentInfo, type ExecutorInfo, type SessionInfo, api } from "@/lib/api";
import { type ParsedJournal, type ParsedSnapshot, parseJournal, parseSnapshot } from "@/lib/parse-agent";
import { DetailPanel, ExecutorTable, type SortDir, type SortKey } from "@/pages/Executors";

// ── Tabs ──

const TABS = [
  { id: "overview", label: "Overview", icon: Zap },
  { id: "strategy", label: "Strategy", icon: Brain },
  { id: "learnings", label: "Learnings", icon: Lightbulb },
  { id: "sessions", label: "Sessions", icon: Clock },
  { id: "experiments", label: "Dry-Run", icon: FlaskConical },
] as const;

type TabId = (typeof TABS)[number]["id"];

// ── Status Controls ──

function StartSessionDialog({
  open,
  onClose,
  slug,
  agentConfig,
  defaultContext,
}: {
  open: boolean;
  onClose: () => void;
  slug: string;
  agentConfig: Record<string, unknown>;
  defaultContext: string;
}) {
  const queryClient = useQueryClient();
  const riskDefaults = (agentConfig.risk_limits || {}) as Record<string, unknown>;

  const [executionMode, setExecutionMode] = useState<"dry_run" | "run_once" | "loop">("loop");
  const [context, setContext] = useState(defaultContext);
  const [serverName, setServerName] = useState((agentConfig.server_name as string) || "");
  const [totalAmountQuote, setTotalAmountQuote] = useState(String(agentConfig.total_amount_quote ?? 100));
  const [frequencySec, setFrequencySec] = useState(String(agentConfig.frequency_sec ?? 60));
  const [maxPositionSize, setMaxPositionSize] = useState(String(riskDefaults.max_position_size_quote ?? 500));
  const [maxOpenExecutors, setMaxOpenExecutors] = useState(String(riskDefaults.max_open_executors ?? 5));
  const [maxDrawdown, setMaxDrawdown] = useState(String(riskDefaults.max_drawdown_pct ?? -1));

  const { data: servers } = useQuery({
    queryKey: ["servers"],
    queryFn: () => api.getServers(),
    enabled: open,
  });

  const startMut = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> = {
        server_name: serverName,
        total_amount_quote: Number(totalAmountQuote) || 100,
        frequency_sec: Number(frequencySec) || 60,
        execution_mode: executionMode,
        risk_limits: {
          max_position_size_quote: Number(maxPositionSize) || 500,
          max_open_executors: Number(maxOpenExecutors) || 5,
          max_drawdown_pct: Number(maxDrawdown),
        },
      };
      return api.startAgent(slug, config, context);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      onClose();
    },
  });

  if (!open) return null;

  const inputClass =
    "w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]";
  const labelClass = "mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">Start New Session</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-5">
          {/* Execution Mode */}
          <div>
            <label className={labelClass}>Execution Mode</label>
            <div className="flex gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
              {([
                { value: "dry_run", label: "Dry Run", desc: "Simulate" },
                { value: "run_once", label: "Run Once", desc: "Single tick" },
                { value: "loop", label: "Loop", desc: "Continuous" },
              ] as const).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setExecutionMode(opt.value)}
                  className={`flex-1 rounded-md px-3 py-2 text-center text-xs font-medium transition-all ${
                    executionMode === opt.value
                      ? opt.value === "dry_run"
                        ? "bg-blue-500/15 text-blue-400"
                        : opt.value === "run_once"
                          ? "bg-amber-500/15 text-amber-400"
                          : "bg-emerald-500/15 text-emerald-400"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  <div>{opt.label}</div>
                  <div className="mt-0.5 text-[10px] opacity-60">{opt.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Trading Context */}
          <div>
            <label className={labelClass}>
              <MessageSquareText className="h-3.5 w-3.5" />
              Trading Context
            </label>
            <p className="mb-2 text-xs text-[var(--color-text-muted)]">
              Describe what this session should focus on. This guides the agent's trading decisions.
            </p>
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="e.g. Focus on SOL meme coins, ride momentum for 5-10% gains, tight stops at 3%..."
              rows={3}
              className={`${inputClass} resize-none`}
              autoFocus
            />
          </div>

          {/* Server row */}
          <div>
            <label className={labelClass}>
              <Server className="h-3.5 w-3.5" />
              Server
            </label>
            <select
              value={serverName}
              onChange={(e) => setServerName(e.target.value)}
              className={inputClass}
            >
              <option value="">Auto (current default)</option>
              {servers?.map((s) => (
                <option key={s.name} value={s.name} disabled={!s.online}>
                  {s.name} {s.online ? "" : "(offline)"}
                </option>
              ))}
            </select>
          </div>

          {/* Budget + Frequency row */}
          <div className={`grid gap-4 ${executionMode === "loop" ? "grid-cols-2" : "grid-cols-1"}`}>
            <div>
              <label className={labelClass}>
                Budget (USDT)
              </label>
              <input
                type="number"
                min={1}
                step={10}
                value={totalAmountQuote}
                onChange={(e) => setTotalAmountQuote(e.target.value)}
                className={inputClass}
              />
            </div>
            {executionMode === "loop" && (
              <div>
                <label className={labelClass}>
                  <Clock className="h-3.5 w-3.5" />
                  Frequency (sec)
                </label>
                <input
                  type="number"
                  min={10}
                  value={frequencySec}
                  onChange={(e) => setFrequencySec(e.target.value)}
                  className={inputClass}
                />
              </div>
            )}
          </div>

          {/* Risk Limits */}
          <div>
            <label className={`${labelClass} mb-3`}>
              <Zap className="h-3.5 w-3.5" />
              Risk Limits
            </label>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <span className="mb-1 block text-[10px] text-[var(--color-text-muted)]">Max Position ($)</span>
                <input
                  type="number"
                  min={0}
                  value={maxPositionSize}
                  onChange={(e) => setMaxPositionSize(e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <span className="mb-1 block text-[10px] text-[var(--color-text-muted)]">Max Executors</span>
                <input
                  type="number"
                  min={1}
                  value={maxOpenExecutors}
                  onChange={(e) => setMaxOpenExecutors(e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <span className="mb-1 block text-[10px] text-[var(--color-text-muted)]">Max Drawdown %</span>
                <input
                  type="number"
                  value={maxDrawdown}
                  onChange={(e) => setMaxDrawdown(e.target.value)}
                  className={inputClass}
                />
              </div>
            </div>
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
            onClick={() => startMut.mutate()}
            disabled={startMut.isPending}
            className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium text-white transition-all disabled:opacity-40 ${
              executionMode === "dry_run" ? "bg-blue-600 hover:bg-blue-500" : executionMode === "run_once" ? "bg-amber-600 hover:bg-amber-500" : "bg-emerald-600 hover:bg-emerald-500"
            }`}
          >
            <Play className="h-3.5 w-3.5" />
            {startMut.isPending
              ? "Starting..."
              : executionMode === "dry_run"
                ? "Run Dry Test"
                : executionMode === "run_once"
                  ? "Execute Once"
                  : "Start Session"}
          </button>
        </div>
      </div>
    </div>
  );
}

function AgentControls({ slug, status, defaultContext, agentConfig }: { slug: string; status: string; defaultContext: string; agentConfig: Record<string, unknown> }) {
  const queryClient = useQueryClient();
  const [showStartDialog, setShowStartDialog] = useState(false);

  const stopMut = useMutation({
    mutationFn: () => api.stopAgent(slug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent", slug] }),
  });
  const pauseMut = useMutation({
    mutationFn: () => api.pauseAgent(slug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent", slug] }),
  });
  const resumeMut = useMutation({
    mutationFn: () => api.resumeAgent(slug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent", slug] }),
  });

  const loading = stopMut.isPending || pauseMut.isPending || resumeMut.isPending;

  return (
    <>
      <div className="flex items-center gap-2">
        {status === "idle" || status === "stopped" ? (
          <button
            onClick={() => setShowStartDialog(true)}
            className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition-all hover:bg-emerald-500"
          >
            <Play className="h-3.5 w-3.5" /> Start
          </button>
        ) : status === "running" ? (
          <>
            <button
              onClick={() => pauseMut.mutate()}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-400 transition-all hover:bg-amber-500/20 disabled:opacity-40"
            >
              <Pause className="h-3.5 w-3.5" /> Pause
            </button>
            <button
              onClick={() => stopMut.mutate()}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:opacity-40"
            >
              <Square className="h-3.5 w-3.5" /> Stop
            </button>
          </>
        ) : status === "paused" ? (
          <>
            <button
              onClick={() => resumeMut.mutate()}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition-all hover:bg-emerald-500 disabled:opacity-40"
            >
              <Play className="h-3.5 w-3.5" /> Resume
            </button>
            <button
              onClick={() => stopMut.mutate()}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:opacity-40"
            >
              <Square className="h-3.5 w-3.5" /> Stop
            </button>
          </>
        ) : null}
      </div>

      <StartSessionDialog
        open={showStartDialog}
        onClose={() => setShowStartDialog(false)}
        slug={slug}
        agentConfig={agentConfig}
        defaultContext={defaultContext}
      />
    </>
  );
}

// ── Overview Tab ──

function InstanceCard({ instance }: { instance: import("@/lib/api").RunningInstance }) {
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

function PerformancePanel({ slug }: { slug: string }) {
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

function OverviewTab({ agent }: { agent: AgentDetailType }) {
  const config = agent.config as Record<string, unknown>;
  const riskLimits = (config.risk_limits || {}) as Record<string, unknown>;
  const defaultTradingContext = agent.default_trading_context || "";
  const instances = agent.instances || [];
  const hasRunning = instances.length > 0;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <PerformancePanel slug={agent.slug} />

      {/* Running Instances — shown when there are active sessions */}
      {hasRunning && (
        <div className="rounded-lg border border-emerald-500/20 bg-[var(--color-surface)] p-4 lg:col-span-2">
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

      {/* Default Trading Context */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 lg:col-span-2">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <MessageSquareText className="h-3.5 w-3.5" /> Default Trading Context
        </h3>
        {defaultTradingContext ? (
          <p className="whitespace-pre-wrap rounded-md bg-[var(--color-bg)] p-3 text-sm leading-relaxed text-[var(--color-text-muted)]">
            {defaultTradingContext}
          </p>
        ) : (
          <p className="text-sm text-[var(--color-text-muted)]">
            No default trading context set. You can set one per session when starting.
          </p>
        )}
      </div>

      {/* Default Config */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Settings className="h-3.5 w-3.5" /> Default Configuration
        </h3>
        <div className="space-y-2 font-mono text-sm">
          {Object.entries(config)
            .filter(([k]) => k !== "risk_limits" && k !== "trading_context")
            .map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">{k}</span>
                <span className="text-[var(--color-text)]">{String(v)}</span>
              </div>
            ))}
        </div>
      </div>

      {/* Default Risk Limits */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Zap className="h-3.5 w-3.5" /> Default Risk Limits
        </h3>
        <div className="space-y-2 font-mono text-sm">
          {Object.entries(riskLimits).map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <span className="text-[var(--color-text-muted)]">{k}</span>
              <span className="text-[var(--color-text)]">{String(v)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Quick Stats */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 lg:col-span-2">
        <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          Agent Info
        </h3>
        <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Sessions</span>
            <span className="text-lg font-semibold text-[var(--color-text)]">{agent.sessions.length}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Status</span>
            <span className={`text-lg font-semibold ${
              agent.status === "running" ? "text-emerald-400" : agent.status === "paused" ? "text-amber-400" : "text-[var(--color-text-muted)]"
            }`}>
              {agent.status.toUpperCase()}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Agent ID</span>
            <span className="font-mono text-sm text-[var(--color-text)]">{agent.agent_id || "--"}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Slug</span>
            <span className="font-mono text-sm text-[var(--color-text)]">{agent.slug}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Strategy Tab (Markdown Editor) ──

function StrategyTab({ slug, content }: { slug: string; content: string }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(content);
  const [dirty, setDirty] = useState(false);

  const saveMut = useMutation({
    mutationFn: () => api.updateAgentMd(slug, value),
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
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-[var(--color-text-muted)]">agent.md</span>
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

// ── Learnings Tab ──

function LearningsTab({ slug, content }: { slug: string; content: string }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(content);
  const [dirty, setDirty] = useState(false);

  const saveMut = useMutation({
    mutationFn: () => api.updateAgentLearnings(slug, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      setDirty(false);
    },
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-[var(--color-text-muted)]">learnings.md — persists across sessions</span>
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
        onChange={(e) => { setValue(e.target.value); setDirty(true); }}
        spellCheck={false}
        className="min-h-[400px] w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 font-mono text-sm leading-relaxed text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]/50"
      />
    </div>
  );
}

// ── Session Selector ──

function SessionSelector({
  sessions,
  selectedSessionNum,
  onSelect,
}: {
  sessions: SessionInfo[];
  selectedSessionNum: number;
  onSelect: (num: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const selected = sessions.find((s) => s.number === selectedSessionNum);
  const sortedSessions = sessions; // Backend already returns newest-first

  return (
    <div ref={ref} className="relative sm:w-72">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5 text-left text-sm transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
      >
        <div className="flex items-center gap-2.5 overflow-hidden">
          <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md bg-[var(--color-primary)]/10 text-xs font-bold text-[var(--color-primary)]">
            {selected?.number ?? "–"}
          </div>
          <div className="min-w-0">
            <div className="truncate font-medium text-[var(--color-text)]">
              Session {selected?.number}
            </div>
            <div className="truncate text-xs text-[var(--color-text-muted)]">
              {selected?.created_at ?? "Unknown"} · {selected?.snapshot_count ?? 0} tick{selected?.snapshot_count !== 1 ? "s" : ""}
            </div>
          </div>
        </div>
        <ChevronDown className={`h-4 w-4 flex-shrink-0 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute left-0 z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] shadow-lg">
          {sortedSessions.map((s) => {
            const isActive = s.number === selectedSessionNum;
            return (
              <button
                key={s.number}
                type="button"
                onClick={() => { onSelect(s.number); setOpen(false); }}
                className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-xs font-bold ${
                  isActive ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)]" : "bg-[var(--color-border)]/50 text-[var(--color-text-muted)]"
                }`}>
                  {s.number}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">Session {s.number}</div>
                  <div className="truncate text-xs text-[var(--color-text-muted)]">
                    {s.created_at ?? "Unknown"} · {s.snapshot_count} tick{s.snapshot_count !== 1 ? "s" : ""}
                  </div>
                </div>
                {isActive && <div className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[var(--color-primary)]" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Sessions Tab ──

const SESSION_SUB_TABS = [
  { id: "overview", label: "Overview", icon: LayoutList },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "snapshots", label: "Snapshots", icon: Camera },
] as const;

type SessionSubTabId = (typeof SESSION_SUB_TABS)[number]["id"];

function SessionMetricsBar({
  journal,
  perf,
}: {
  journal: ParsedJournal;
  perf?: import("@/lib/api").AgentPerformance | null;
}) {
  const { summary, metrics, ticks } = journal;
  const lastMetric = metrics.length > 0 ? metrics[metrics.length - 1] : null;
  const pnl = perf ? perf.total_pnl : summary.pnl;
  const openCount = perf ? perf.open_count : summary.openExecutors;
  const volume = perf?.volume;
  const stats = [
    { label: "Ticks", value: String(summary.lastTick || ticks.length), color: "text-[var(--color-text)]" },
    { label: "PnL", value: `$${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`, color: pnl >= 0 ? "text-emerald-400" : "text-red-400" },
    { label: "Open Executors", value: String(openCount), color: "text-[var(--color-text)]" },
    volume !== undefined
      ? { label: "Volume", value: `$${volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: "text-[var(--color-text)]" }
      : { label: "Exposure", value: lastMetric ? `$${lastMetric.exposure.toFixed(2)}` : "$0.00", color: "text-[var(--color-text)]" },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {stats.map((s) => (
        <div key={s.label} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <span className="block text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">{s.label}</span>
          <span className={`text-lg font-semibold ${s.color}`}>{s.value}</span>
        </div>
      ))}
    </div>
  );
}

function SessionOverview({
  journal,
  perf,
}: {
  journal: ParsedJournal;
  perf?: import("@/lib/api").AgentPerformance | null;
}) {
  const { summary, executors, metrics } = journal;
  const pnl = perf ? perf.total_pnl : summary.pnl;

  return (
    <div className="space-y-4">
      {/* Summary Card */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Summary</h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Status</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${
              summary.status === "ACTIVE" || summary.status === "running"
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                : summary.status === "paused"
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-400"
                  : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
            }`}>
              {summary.status || "idle"}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Tick</span>
            <span className="font-mono text-sm text-[var(--color-text)]">#{summary.lastTick}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">PnL</span>
            <span className={`font-mono text-sm ${pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              ${pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Action</span>
            <span className="text-sm text-[var(--color-text)]">{summary.lastAction || "—"}</span>
          </div>
        </div>
      </div>

      {/* Executor Table */}
      {executors.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Executors</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                  <th className="pb-2 pr-3">ID</th>
                  <th className="pb-2 pr-3">Type</th>
                  <th className="pb-2 pr-3">Pair</th>
                  <th className="pb-2 pr-3">Side</th>
                  <th className="pb-2 pr-3 text-right">Amount</th>
                  <th className="pb-2 pr-3">Status</th>
                  <th className="pb-2 pr-3 text-right">PnL</th>
                  <th className="pb-2 text-right">Volume</th>
                </tr>
              </thead>
              <tbody>
                {executors.map((ex, i) => (
                  <tr key={`${ex.id}-${i}`} className="border-b border-[var(--color-border)]/30">
                    <td className="py-2 pr-3 font-mono text-[var(--color-text)]">{ex.id.slice(0, 8)}</td>
                    <td className="py-2 pr-3 text-[var(--color-text-muted)]">{ex.type}</td>
                    <td className="py-2 pr-3 font-mono text-[var(--color-text)]">{ex.pair}</td>
                    <td className="py-2 pr-3">
                      <span className={ex.side.toLowerCase() === "buy" ? "text-emerald-400" : "text-red-400"}>
                        {ex.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-right font-mono text-[var(--color-text)]">${ex.amount.toFixed(2)}</td>
                    <td className="py-2 pr-3">
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${
                        ex.status === "open"
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                          : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                      }`}>
                        {ex.status}
                      </span>
                    </td>
                    <td className={`py-2 pr-3 text-right font-mono ${ex.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {ex.pnl >= 0 ? "+" : ""}{ex.pnl.toFixed(2)}
                    </td>
                    <td className="py-2 text-right font-mono text-[var(--color-text-muted)]">{ex.volume.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Metrics Timeline */}
      {metrics.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Metrics Timeline</h3>
          <div className="max-h-60 space-y-1 overflow-y-auto font-mono text-xs">
            {metrics.map((m, i) => (
              <div key={i} className="flex items-center gap-4 text-[var(--color-text-muted)]">
                <span className="w-32 shrink-0">{m.timestamp}</span>
                <span className={`w-20 text-right ${m.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  ${m.pnl >= 0 ? "+" : ""}{m.pnl.toFixed(2)}
                </span>
                <span className="w-24 text-right">vol ${m.volume.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
                <span className="w-14 text-right">open={m.open}</span>
                <span className="w-24 text-right">exp=${m.exposure.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {executors.length === 0 && metrics.length === 0 && (
        <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No executors or metrics yet.</p>
      )}
    </div>
  );
}

function SessionActivity({ journal }: { journal: ParsedJournal }) {
  const { decisions } = journal;

  if (decisions.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No decisions yet.</p>;
  }

  return (
    <div className="space-y-2">
      {decisions.map((d, i) => (
        <div key={i} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <div className="flex items-start gap-3">
            {d.tick > 0 ? (
              <span className="mt-0.5 shrink-0 rounded-md bg-[var(--color-surface-hover)] px-2 py-0.5 font-mono text-xs font-bold text-[var(--color-text-muted)]">
                #{d.tick}
              </span>
            ) : (
              <span className="mt-0.5 shrink-0 rounded-md bg-red-500/10 px-2 py-0.5 font-mono text-xs font-bold text-red-400">
                ERR
              </span>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-[var(--color-text-muted)]">{d.time}</span>
                <span className="text-sm font-medium text-[var(--color-text)]">{d.action}</span>
                {d.riskNote && (
                  <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-400">
                    {d.riskNote}
                  </span>
                )}
              </div>
              {d.reasoning && (
                <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-muted)]">{d.reasoning}</p>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function SnapshotDetail({ slug, sessionNum, tick }: { slug: string; sessionNum: number; tick: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["agent", slug, "session", sessionNum, "snapshot", tick],
    queryFn: () => api.getSnapshot(slug, sessionNum, tick),
    enabled: tick > 0,
  });

  const parsed = useMemo<ParsedSnapshot | null>(() => {
    if (!data?.content) return null;
    return parseSnapshot(data.content);
  }, [data?.content]);

  if (isLoading) {
    return (
      <div className="flex h-48 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (!parsed) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select a snapshot to view details.</p>;
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-lg font-bold text-[var(--color-text)]">#{parsed.tick}</span>
        <span className="text-sm text-[var(--color-text-muted)]">{parsed.timestamp}</span>
      </div>

      {/* System Prompt (collapsed by default, first for context) */}
      {parsed.systemPrompt && (
        <SystemPromptCard prompt={parsed.systemPrompt} charCount={parsed.systemPromptLength} />
      )}

      {/* Agent Response — primary content */}
      {parsed.agentResponse && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h4 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Agent Response</h4>
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-text)]">
            {parsed.agentResponse}
          </div>
        </div>
      )}

      {/* Tool Calls */}
      {parsed.toolCalls.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h4 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            <Wrench className="h-3 w-3" /> Tool Calls ({parsed.toolCalls.length})
          </h4>
          <div className="flex flex-wrap gap-2">
            {parsed.toolCalls.map((tc) => (
              <ToolCallChip key={tc.number} tc={tc} />
            ))}
          </div>
        </div>
      )}

      {/* Risk + Executor side by side */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {parsed.riskState && (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Risk State</h4>
            <div className="space-y-1 font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {parsed.riskState.split("\n").map((line, i) => {
                const isBlocked = line.includes("BLOCKED");
                const isActive = line.includes("ACTIVE");
                return (
                  <div key={i} className={isBlocked ? "text-red-400" : isActive ? "text-emerald-400" : ""}>
                    {line.replace(/^- /, "")}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {parsed.executorState && (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Executor State</h4>
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {parsed.executorState}
            </pre>
          </div>
        )}
      </div>

      {/* Stats Footer */}
      {parsed.stats.duration > 0 && (
        <div className="flex flex-wrap gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 font-mono text-xs text-[var(--color-text-muted)]">
          <span>Duration: <strong className="text-[var(--color-text)]">{parsed.stats.duration.toFixed(1)}s</strong></span>
        </div>
      )}
    </div>
  );
}

function ToolCallChip({ tc }: { tc: import("@/lib/parse-agent").ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = tc.input || tc.output;
  const isOk = tc.status === "success" || tc.status === "completed";
  const isErr = tc.status === "error";
  const dotColor = isOk ? "bg-emerald-400" : isErr ? "bg-red-400" : "bg-[var(--color-text-muted)]";

  // Shorten tool name: remove mcp__ prefixes for readability
  const shortName = tc.name.replace(/^mcp__\w+__/, "");

  if (!hasDetails) {
    return (
      <div className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)] px-2.5 py-1.5">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
        <span className="font-mono text-[11px] text-[var(--color-text)]">{shortName}</span>
      </div>
    );
  }

  return (
    <div className="w-full rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
          <span className="font-mono text-[11px] text-[var(--color-text)]">{shortName}</span>
        </div>
        {expanded ? <ChevronDown className="h-3 w-3 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3 w-3 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-[var(--color-border)]/30 p-3">
          {tc.input && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Input</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                {tc.input}
              </pre>
            </div>
          )}
          {tc.output && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Output</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                {tc.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SystemPromptCard({ prompt, charCount }: { prompt: string; charCount: number }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between p-4 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">System Prompt</h4>
          <span className="text-[10px] text-[var(--color-text-muted)]">({charCount.toLocaleString()} chars)</span>
        </div>
        {expanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && (
        <pre className="max-h-96 overflow-auto border-t border-[var(--color-border)] p-4 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          {prompt}
        </pre>
      )}
    </div>
  );
}

function agentRowToExecutorInfo(row: AgentExecutorRow): ExecutorInfo {
  return {
    id: row.id,
    type: row.type,
    connector: row.connector || "unknown",
    trading_pair: row.pair,
    side: row.side,
    status: row.status,
    close_type: row.close_type,
    pnl: row.pnl,
    volume: row.volume,
    timestamp: row.timestamp,
    controller_id: row.controller_id,
    cum_fees_quote: row.fees,
    net_pnl_pct: 0,
    entry_price: row.entry_price,
    current_price: row.current_price,
    close_timestamp: row.close_timestamp,
    custom_info: row.custom_info ?? {},
    config: row.config ?? {},
  };
}

function SessionExecutors({ slug, sessionNum, serverName }: { slug: string; sessionNum: number; serverName: string }) {
  const { data: sessionDetail } = useQuery({
    queryKey: ["agent-session-executors", slug, sessionNum],
    queryFn: () => api.getAgentSessionExecutors(slug, sessionNum),
    refetchInterval: 10000,
  });

  const executors = sessionDetail?.executors ?? [];

  // Convert rows to ExecutorInfo for table & chart
  const executorInfos = useMemo(
    () => executors.map(agentRowToExecutorInfo),
    [executors],
  );

  // Group executors by connector:pair for charts
  const chartGroups = useMemo(() => {
    if (!serverName || executorInfos.length === 0) return [];
    const groups = new Map<string, ExecutorInfo[]>();
    for (const ex of executorInfos) {
      if (!ex.trading_pair) continue;
      const key = `${ex.connector}:${ex.trading_pair}`;
      const arr = groups.get(key);
      if (arr) arr.push(ex);
      else groups.set(key, [ex]);
    }
    return Array.from(groups.entries());
  }, [executorInfos, serverName]);

  // Table state
  const [sortKey, setSortKey] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedExecutor, setSelectedExecutor] = useState<ExecutorInfo | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const stoppingIds = useMemo(() => new Set<string>(), []);

  const handleSort = useCallback((key: SortKey) => {
    setSortDir((prev) => (sortKey === key ? (prev === "asc" ? "desc" : "asc") : "desc"));
    setSortKey(key);
  }, [sortKey]);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds((prev) =>
      prev.size === executorInfos.length ? new Set() : new Set(executorInfos.map((e) => e.id)),
    );
  }, [executorInfos]);

  const allSelected = selectedIds.size === executorInfos.length && executorInfos.length > 0;

  if (!sessionDetail) {
    return (
      <div className="flex h-32 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (executors.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No executors for this session.</p>;
  }

  return (
    <div className="space-y-4">
      {/* Executor charts by trading pair */}
      {chartGroups.map(([key, group]) => (
        <ExecutorChart
          key={key}
          server={serverName}
          executors={group}
          connector={group[0].connector}
          tradingPair={group[0].trading_pair}
          height={280}
        />
      ))}

      {/* Executor table + detail panel */}
      <div className="flex">
        <div className={`min-w-0 ${selectedExecutor ? "flex-1" : "w-full"}`}>
          <ExecutorTable
            executors={executorInfos}
            sortKey={sortKey}
            sortDir={sortDir}
            onSort={handleSort}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelect}
            onSelectAll={selectAll}
            allSelected={allSelected}
            onRowClick={setSelectedExecutor}
            selectedExecutorId={selectedExecutor?.id ?? null}
            onStop={() => {}}
            stoppingIds={stoppingIds}
          />
        </div>
        {selectedExecutor && serverName && (
          <DetailPanel
            executor={selectedExecutor}
            server={serverName}
            onClose={() => setSelectedExecutor(null)}
            onStop={() => {}}
            stopping={false}
          />
        )}
      </div>
    </div>
  );
}

function SessionSnapshots({ slug, sessionNum }: { slug: string; sessionNum: number }) {
  const [selectedTick, setSelectedTick] = useState<number>(0);

  const { data: snapshotsData } = useQuery({
    queryKey: ["agent", slug, "session", sessionNum, "snapshots"],
    queryFn: () => api.getSessionSnapshots(slug, sessionNum),
  });

  const snapshots = snapshotsData?.snapshots || [];

  if (snapshots.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No snapshots yet.</p>;
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* Snapshot list */}
      <div className="w-full shrink-0 lg:w-72">
        <div className="max-h-[600px] space-y-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2">
          {snapshots.map((snap) => (
            <button
              key={snap.tick}
              onClick={() => setSelectedTick(snap.tick)}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left transition-colors ${
                selectedTick === snap.tick
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs font-bold">#{snap.tick}</span>
                <span className="text-[10px]">{snap.timestamp}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Snapshot detail */}
      <div className="min-w-0 flex-1">
        {selectedTick > 0 ? (
          <SnapshotDetail slug={slug} sessionNum={sessionNum} tick={selectedTick} />
        ) : (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select a snapshot to view details.</p>
        )}
      </div>
    </div>
  );
}

function SessionsTab({ slug, sessions, serverName }: { slug: string; sessions: SessionInfo[]; serverName: string }) {
  const [selectedSessionNum, setSelectedSessionNum] = useState<number>(
    sessions.length > 0 ? sessions[0].number : 0
  );
  const [activeSubTab, setActiveSubTab] = useState<SessionSubTabId>("overview");

  const selectedSession = sessions.find((s) => s.number === selectedSessionNum);

  const { data: journalData } = useQuery({
    queryKey: ["agent", slug, "session", selectedSessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, selectedSessionNum),
    enabled: selectedSessionNum > 0,
  });

  const parsedJournal = useMemo<ParsedJournal | null>(() => {
    if (!journalData?.content) return null;
    return parseJournal(journalData.content);
  }, [journalData?.content]);

  const { data: sessionPerfData } = useQuery({
    queryKey: ["agent-session-executors", slug, selectedSessionNum],
    queryFn: () => api.getAgentSessionExecutors(slug, selectedSessionNum),
    enabled: selectedSessionNum > 0,
    refetchInterval: 10000,
  });
  const sessionPerf = sessionPerfData?.performance ?? null;

  if (sessions.length === 0) {
    return (
      <div className="flex h-48 flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] text-[var(--color-text-muted)]">
        <Clock className="mb-2 h-8 w-8 opacity-30" />
        <p className="text-sm">No sessions yet. Start the agent to create one.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Session selector + metrics */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:gap-4">
        <SessionSelector
          sessions={sessions}
          selectedSessionNum={selectedSessionNum}
          onSelect={(num) => { setSelectedSessionNum(num); setActiveSubTab("overview"); }}
        />
      </div>

      {parsedJournal && <SessionMetricsBar journal={parsedJournal} perf={sessionPerf} />}

      {/* Sub-tab bar */}
      <div className="flex gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
        {SESSION_SUB_TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveSubTab(id)}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
              activeSubTab === id
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Sub-tab content */}
      {!parsedJournal ? (
        <div className="flex h-32 items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
        </div>
      ) : (
        <>
          {activeSubTab === "overview" && selectedSession && (
            <div className="space-y-4">
              <SessionExecutors slug={slug} sessionNum={selectedSession.number} serverName={serverName} />
              <SessionOverview journal={parsedJournal} perf={sessionPerf} />
            </div>
          )}
          {activeSubTab === "overview" && !selectedSession && (
            <SessionOverview journal={parsedJournal} perf={sessionPerf} />
          )}
          {activeSubTab === "activity" && <SessionActivity journal={parsedJournal} />}
          {activeSubTab === "snapshots" && selectedSession && (
            <SessionSnapshots slug={slug} sessionNum={selectedSession.number} />
          )}
        </>
      )}
    </div>
  );
}

// ── Experiments Tab ──

function ExperimentsTab({ slug, experiments }: { slug: string; experiments: ExperimentInfo[] }) {
  const [selectedExpNum, setSelectedExpNum] = useState<number>(
    experiments.length > 0 ? experiments[0].number : 0
  );

  const { data: snapshotData, isLoading } = useQuery({
    queryKey: ["agent", slug, "experiment", selectedExpNum],
    queryFn: () => api.getExperiment(slug, selectedExpNum),
    enabled: selectedExpNum > 0,
  });

  const parsed = useMemo<ParsedSnapshot | null>(() => {
    if (!snapshotData?.content) return null;
    return parseSnapshot(snapshotData.content);
  }, [snapshotData?.content]);

  if (experiments.length === 0) {
    return (
      <div className="flex h-48 flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] text-[var(--color-text-muted)]">
        <FlaskConical className="mb-2 h-8 w-8 opacity-30" />
        <p className="text-sm">No experiments yet. Run a dry-run or run-once to create one.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* Experiment list */}
      <div className="w-full shrink-0 lg:w-80">
        <div className="max-h-[700px] space-y-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2">
          {experiments.map((exp) => {
            const isActive = exp.number === selectedExpNum;
            const modeLabel = exp.execution_mode === "dry_run" ? "Dry Run" : exp.execution_mode === "run_once" ? "Run Once" : exp.execution_mode;
            const modeColor = exp.execution_mode === "dry_run"
              ? "bg-amber-500/10 text-amber-400"
              : "bg-blue-500/10 text-blue-400";
            return (
              <button
                key={exp.number}
                onClick={() => setSelectedExpNum(exp.number)}
                className={`flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-xs font-bold ${
                  isActive ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)]" : "bg-[var(--color-border)]/50 text-[var(--color-text-muted)]"
                }`}>
                  {exp.number}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">Experiment {exp.number}</span>
                    <span className={`inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-medium ${modeColor}`}>
                      {modeLabel}
                    </span>
                    {exp.agent_key && (
                      <span className="inline-flex rounded-full bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-primary)]">
                        {exp.agent_key}
                      </span>
                    )}
                  </div>
                </div>
                {isActive && <div className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[var(--color-primary)]" />}
              </button>
            );
          })}
        </div>
      </div>

      {/* Snapshot detail */}
      <div className="min-w-0 flex-1">
        {isLoading ? (
          <div className="flex h-48 items-center justify-center">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
          </div>
        ) : parsed ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-lg font-bold text-[var(--color-text)]">Experiment #{selectedExpNum}</span>
              <span className="text-sm text-[var(--color-text-muted)]">{parsed.timestamp}</span>
            </div>
            {parsed.agentResponse && (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Agent Response</h3>
                <div className="whitespace-pre-wrap text-sm text-[var(--color-text)]">{parsed.agentResponse}</div>
              </div>
            )}
            {parsed.toolCalls.length > 0 && (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                  Tool Calls ({parsed.toolCalls.length})
                </h3>
                <div className="space-y-2">
                  {parsed.toolCalls.map((tc, i) => (
                    <div key={i} className="rounded-md bg-[var(--color-bg)]/50 px-3 py-2 text-xs">
                      <span className="font-medium text-[var(--color-primary)]">{tc.name}</span>
                      <span className="ml-2 text-[var(--color-text-muted)]">({tc.status})</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {parsed.riskState && (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Risk State</h3>
                <div className="whitespace-pre-wrap text-xs text-[var(--color-text-muted)]">{parsed.riskState}</div>
              </div>
            )}
          </div>
        ) : (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select an experiment to view its snapshot.</p>
        )}
      </div>
    </div>
  );
}

// ── Main Page ──

export function AgentDetail() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TabId>("overview");

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

  return (
    <div className="mx-auto w-full max-w-7xl">
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={() => navigate("/agents")}
          className="mb-3 flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
        </button>

        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[var(--color-text)]">{agent.name}</h1>
            {agent.description && (
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">{agent.description}</p>
            )}
          </div>
          <AgentControls slug={slug!} status={agent.status} defaultContext={agent.default_trading_context || (agent.config.trading_context as string) || ""} agentConfig={agent.config} />
        </div>
      </div>

      {/* Tab bar */}
      <div className="mb-6 flex gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
              activeTab === id
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "overview" && <OverviewTab agent={agent} />}
      {activeTab === "strategy" && <StrategyTab slug={slug!} content={agent.agent_md} />}
      {activeTab === "learnings" && <LearningsTab slug={slug!} content={agent.learnings} />}
      {activeTab === "sessions" && <SessionsTab slug={slug!} sessions={agent.sessions} serverName={(agent.config.server_name as string) || ""} />}
      {activeTab === "experiments" && <ExperimentsTab slug={slug!} experiments={agent.experiments || []} />}
    </div>
  );
}
