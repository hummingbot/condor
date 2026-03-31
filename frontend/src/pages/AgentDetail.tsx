import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BookOpen,
  Brain,
  ChevronDown,
  ChevronRight,
  Clock,
  Eye,
  Lightbulb,
  MessageSquareText,
  Pause,
  Play,
  Save,
  Server,
  Settings,
  Square,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { type AgentDetail as AgentDetailType, type SessionInfo, type SnapshotSummary, api } from "@/lib/api";

// ── Tabs ──

const TABS = [
  { id: "overview", label: "Overview", icon: Zap },
  { id: "strategy", label: "Strategy", icon: Brain },
  { id: "learnings", label: "Learnings", icon: Lightbulb },
  { id: "sessions", label: "Sessions", icon: Clock },
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
          <div className="grid grid-cols-2 gap-4">
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
            className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-emerald-500 disabled:opacity-40"
          >
            <Play className="h-3.5 w-3.5" />
            {startMut.isPending ? "Starting..." : "Start Session"}
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

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-bold text-[var(--color-text)]">{instance.agent_id}</span>
          <span className={`text-xs font-semibold uppercase ${statusColor}`}>{instance.status}</span>
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

function OverviewTab({ agent }: { agent: AgentDetailType }) {
  const config = agent.config as Record<string, unknown>;
  const riskLimits = (config.risk_limits || {}) as Record<string, unknown>;
  const defaultTradingContext = agent.default_trading_context || "";
  const instances = agent.instances || [];
  const hasRunning = instances.length > 0;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
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

// ── Sessions Tab ──

function SessionCard({ slug, session }: { slug: string; session: SessionInfo }) {
  const [expanded, setExpanded] = useState(false);
  const [showSnapshots, setShowSnapshots] = useState(false);

  const { data: journal } = useQuery({
    queryKey: ["agent", slug, "session", session.number, "journal"],
    queryFn: () => api.getSessionJournal(slug, session.number),
    enabled: expanded,
  });

  const { data: snapshotsData } = useQuery({
    queryKey: ["agent", slug, "session", session.number, "snapshots"],
    queryFn: () => api.getSessionSnapshots(slug, session.number),
    enabled: showSnapshots,
  });

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between p-4 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--color-surface-hover)] font-mono text-sm font-bold text-[var(--color-text-muted)]">
            {session.number}
          </div>
          <div>
            <span className="text-sm font-medium text-[var(--color-text)]">Session {session.number}</span>
            <span className="ml-2 text-xs text-[var(--color-text-muted)]">
              {session.snapshot_count} snapshot{session.snapshot_count !== 1 ? "s" : ""}
            </span>
          </div>
        </div>
        {expanded ? <ChevronDown className="h-4 w-4 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-4 w-4 text-[var(--color-text-muted)]" />}
      </button>

      {expanded && (
        <div className="border-t border-[var(--color-border)] p-4">
          {/* Journal */}
          <div className="mb-4">
            <h4 className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
              <BookOpen className="h-3 w-3" /> Journal
            </h4>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-[var(--color-bg)] p-3 font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {journal?.content || "Loading..."}
            </pre>
          </div>

          {/* Snapshots toggle */}
          <button
            onClick={() => setShowSnapshots(!showSnapshots)}
            className="flex items-center gap-2 text-xs font-semibold text-[var(--color-primary)] transition-colors hover:text-[var(--color-primary)]/80"
          >
            <Eye className="h-3.5 w-3.5" />
            {showSnapshots ? "Hide Snapshots" : `View Snapshots (${session.snapshot_count})`}
          </button>

          {showSnapshots && snapshotsData && (
            <div className="mt-3 space-y-2">
              {snapshotsData.snapshots.length === 0 ? (
                <p className="text-xs text-[var(--color-text-muted)]">No snapshots yet.</p>
              ) : (
                snapshotsData.snapshots.map((snap) => (
                  <SnapshotItem key={snap.tick} slug={slug} sessionNum={session.number} snapshot={snap} />
                ))
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SnapshotItem({
  slug,
  sessionNum,
  snapshot,
}: {
  slug: string;
  sessionNum: number;
  snapshot: SnapshotSummary;
}) {
  const [expanded, setExpanded] = useState(false);

  const { data } = useQuery({
    queryKey: ["agent", slug, "session", sessionNum, "snapshot", snapshot.tick],
    queryFn: () => api.getSnapshot(slug, sessionNum, snapshot.tick),
    enabled: expanded,
  });

  return (
    <div className="rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
      >
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold text-[var(--color-text)]">#{snapshot.tick}</span>
          <span className="text-xs text-[var(--color-text-muted)]">{snapshot.timestamp}</span>
          {snapshot.cost > 0 && (
            <span className="text-xs text-[var(--color-text-muted)]">${snapshot.cost.toFixed(4)}</span>
          )}
        </div>
        {expanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && data && (
        <pre className="max-h-96 overflow-auto border-t border-[var(--color-border)]/30 p-3 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          {data.content}
        </pre>
      )}
    </div>
  );
}

function SessionsTab({ slug, sessions }: { slug: string; sessions: SessionInfo[] }) {
  if (sessions.length === 0) {
    return (
      <div className="flex h-48 flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] text-[var(--color-text-muted)]">
        <Clock className="mb-2 h-8 w-8 opacity-30" />
        <p className="text-sm">No sessions yet. Start the agent to create one.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {sessions.map((session) => (
        <SessionCard key={session.number} slug={slug} session={session} />
      ))}
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
    <div className="mx-auto max-w-5xl">
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
      {activeTab === "sessions" && <SessionsTab slug={slug!} sessions={agent.sessions} />}
    </div>
  );
}
