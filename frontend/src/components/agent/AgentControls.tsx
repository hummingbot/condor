import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Clock,
  MessageSquareText,
  Pause,
  Play,
  Power,
  Server,
  Square,
  X,
  Zap,
} from "lucide-react";
import { useState } from "react";

import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api } from "@/lib/api";

// ── Start Session Dialog ──

export function StartSessionDialog({
  open,
  onClose,
  slug,
  sslug,
  agentConfig,
  defaultContext,
}: {
  open: boolean;
  onClose: () => void;
  slug: string;
  sslug: string;
  agentConfig: Record<string, unknown>;
  defaultContext: string;
}) {
  const queryClient = useQueryClient();
  useEscapeKey(open, onClose);
  const riskDefaults = (agentConfig.risk_limits || {}) as Record<string, unknown>;

  const [executionMode, setExecutionMode] = useState<"dry_run" | "run_once" | "loop">("loop");
  const [context, setContext] = useState(defaultContext);
  const [serverName, setServerName] = useState((agentConfig.server_name as string) || "");
  const [totalAmountQuote, setTotalAmountQuote] = useState(String(agentConfig.total_amount_quote ?? 100));
  const [frequencySec, setFrequencySec] = useState(String(agentConfig.frequency_sec ?? 60));
  // 0 in the config means "use the backend default" (10 min); show the resolved
  // number so the field never reads as an unlimited budget.
  const [tickTimeoutSec, setTickTimeoutSec] = useState(
    String(Number(agentConfig.tick_timeout_sec) || 600),
  );
  const [maxPositionSize, setMaxPositionSize] = useState(String(riskDefaults.max_position_size_quote ?? 500));
  const [maxOpenExecutors, setMaxOpenExecutors] = useState(String(riskDefaults.max_open_executors ?? 5));
  const [maxDrawdown, setMaxDrawdown] = useState(String(riskDefaults.max_drawdown_pct ?? -1));
  // Seeded from the strategy's stored answer, so a strategy already opted in
  // does not quietly start opted out every time someone opens this dialog.
  const [restartOnBoot, setRestartOnBoot] = useState(!!agentConfig.restart_on_boot);

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
        tick_timeout_sec: Number(tickTimeoutSec) || 600,
        execution_mode: executionMode,
        // Only a loop can be resumed: a dry run and a single tick are finished
        // by the time a restart could resume them, and recording the opt-in on
        // one would arm the boot pass for a run that had already ended.
        restart_on_boot: executionMode === "loop" && restartOnBoot,
        risk_limits: {
          max_position_size_quote: Number(maxPositionSize) || 500,
          max_open_executors: Number(maxOpenExecutors) || 5,
          max_drawdown_pct: Number(maxDrawdown),
        },
      };
      return api.startStrategy(slug, sslug, config, context);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
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
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]" title="Close" aria-label="Close">
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

          {/* Budget + Frequency + Tick timeout row */}
          <div className={`grid gap-4 ${executionMode === "loop" ? "grid-cols-3" : "grid-cols-2"}`}>
            <div>
              <label className={labelClass}>
                Total Amount Quote
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
            <div>
              <label className={labelClass} title="Wall-clock budget for one tick's agent session">
                <Clock className="h-3.5 w-3.5" />
                Tick Timeout (sec)
              </label>
              <input
                type="number"
                min={30}
                step={30}
                value={tickTimeoutSec}
                onChange={(e) => setTickTimeoutSec(e.target.value)}
                className={inputClass}
              />
            </div>
          </div>

          {/* Survives a restart? Only a loop can, so the row is only there for
              one. Phrased as what happens rather than as a flag name: the
              reader this exists for is the one who restarted Condor and was
              surprised to find the loop gone. */}
          {executionMode === "loop" && (
            <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
              <input
                type="checkbox"
                checked={restartOnBoot}
                onChange={(e) => setRestartOnBoot(e.target.checked)}
                className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--color-primary)]"
              />
              <span>
                <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text)]">
                  <Power className="h-3.5 w-3.5" />
                  Resume after Condor restarts
                </span>
                <span className="mt-1 block text-[11px] leading-snug text-[var(--color-text-muted)]">
                  A restart otherwise leaves this loop stopped. On, Condor starts
                  it again in a <strong>fresh session</strong>, from the config as
                  it stands then — it never resumes the old session&apos;s journal.
                </span>
              </span>
            </label>
          )}

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
        {startMut.isError && (
          <p className="mt-3 text-xs text-red-400">{startMut.error.message}</p>
        )}
      </div>
    </div>
  );
}

// ── Agent Controls ──

export function AgentControls({ slug, sslug, status, defaultContext, agentConfig }: { slug: string; sslug: string; status: string; defaultContext: string; agentConfig: Record<string, unknown> }) {
  const queryClient = useQueryClient();
  const [showStartDialog, setShowStartDialog] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  const stopMut = useMutation({
    mutationFn: () => api.stopStrategy(slug, sslug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
      setConfirmStop(false);
    },
  });
  const pauseMut = useMutation({
    mutationFn: () => api.pauseStrategy(slug, sslug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] }),
  });
  const resumeMut = useMutation({
    mutationFn: () => api.resumeStrategy(slug, sslug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] }),
  });

  const loading = stopMut.isPending || pauseMut.isPending || resumeMut.isPending;
  const controlError = stopMut.error || pauseMut.error || resumeMut.error;

  const stopControls = confirmStop ? (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => stopMut.mutate()}
        disabled={stopMut.isPending}
        className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white transition-all hover:bg-red-500 disabled:opacity-40"
      >
        {stopMut.isPending ? "Stopping..." : "Confirm"}
      </button>
      <button
        onClick={() => setConfirmStop(false)}
        disabled={stopMut.isPending}
        className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)] disabled:opacity-40"
      >
        Cancel
      </button>
    </div>
  ) : (
    <button
      onClick={() => setConfirmStop(true)}
      disabled={loading}
      className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:opacity-40"
    >
      <Square className="h-3.5 w-3.5" /> Stop
    </button>
  );

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
            {stopControls}
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
            {stopControls}
          </>
        ) : null}
        {controlError && (
          <p className="text-xs text-red-400">{controlError.message}</p>
        )}
      </div>

      <StartSessionDialog
        open={showStartDialog}
        onClose={() => setShowStartDialog(false)}
        slug={slug}
        sslug={sslug}
        agentConfig={agentConfig}
        defaultContext={defaultContext}
      />
    </>
  );
}
