import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Square, X } from "lucide-react";
import { useEffect, useState } from "react";

import {
  BudgetFrequencyFields,
  DigestIntervalField,
  ExecutionModePicker,
  RiskLimitsFields,
  ServerSelect,
  TradingContextField,
  buildRiskLimitsPayload,
  type ExecutionMode,
} from "@/components/agent/AgentSessionConfigFields";
import { StrategyParamsForm } from "@/components/agent/StrategyParamsForm";
import { api } from "@/lib/api";

function readRiskLimit(defaults: Record<string, unknown>, key: string, fallback: number) {
  const risk = (defaults.risk_limits || {}) as Record<string, unknown>;
  const value = risk[key];
  return value !== undefined && value !== null ? String(value) : String(fallback);
}

// ── Start Session Dialog ──

export function StartSessionDialog({
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

  const [executionMode, setExecutionMode] = useState<ExecutionMode>(
    (agentConfig.execution_mode as ExecutionMode) || "loop",
  );
  const [context, setContext] = useState(defaultContext);
  const [serverName, setServerName] = useState((agentConfig.server_name as string) || "");
  const [totalAmountQuote, setTotalAmountQuote] = useState(String(agentConfig.total_amount_quote ?? 100));
  const [frequencySec, setFrequencySec] = useState(String(agentConfig.frequency_sec ?? 60));
  const [digestIntervalTicks, setDigestIntervalTicks] = useState(
    String(agentConfig.digest_interval_ticks ?? 0),
  );
  const [maxOpenExecutors, setMaxOpenExecutors] = useState(
    readRiskLimit(agentConfig, "max_open_executors", 5),
  );
  const [maxDrawdown, setMaxDrawdown] = useState(readRiskLimit(agentConfig, "max_drawdown_pct", -1));
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>({});

  const { data: strategySchema } = useQuery({
    queryKey: ["strategy-config-schema", slug],
    queryFn: () => api.getStrategyConfigSchema(slug),
    enabled: open,
  });

  useEffect(() => {
    if (!open) return;
    setExecutionMode((agentConfig.execution_mode as ExecutionMode) || "loop");
    setContext(defaultContext);
    setServerName((agentConfig.server_name as string) || "");
    setTotalAmountQuote(String(agentConfig.total_amount_quote ?? 100));
    setFrequencySec(String(agentConfig.frequency_sec ?? 60));
    setDigestIntervalTicks(String(agentConfig.digest_interval_ticks ?? 0));
    setMaxOpenExecutors(readRiskLimit(agentConfig, "max_open_executors", 5));
    setMaxDrawdown(readRiskLimit(agentConfig, "max_drawdown_pct", -1));
    const saved = agentConfig.strategy_params;
    setStrategyParams(
      typeof saved === "object" && saved !== null ? { ...(saved as Record<string, unknown>) } : {},
    );
  }, [open, agentConfig, defaultContext]);

  const handleStrategyParamChange = (key: string, value: unknown) => {
    setStrategyParams((prev) => ({ ...prev, [key]: value }));
  };

  const startMut = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> = {
        server_name: serverName,
        total_amount_quote: Number(totalAmountQuote) || 100,
        frequency_sec: Number(frequencySec) || 60,
        digest_interval_ticks: Math.max(0, Number(digestIntervalTicks) || 0),
        execution_mode: executionMode,
        risk_limits: buildRiskLimitsPayload(maxOpenExecutors, maxDrawdown),
        ...(strategySchema && Object.keys(strategySchema.fields).length > 0
          ? { strategy_params: strategyParams }
          : {}),
      };
      return api.startAgent(slug, config, context);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      onClose();
    },
  });

  if (!open) return null;

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
          <ExecutionModePicker value={executionMode} onChange={setExecutionMode} />

          <TradingContextField
            value={context}
            onChange={setContext}
            autoFocus
          />

          <ServerSelect value={serverName} onChange={setServerName} enabled={open} />

          <BudgetFrequencyFields
            executionMode={executionMode}
            totalAmountQuote={totalAmountQuote}
            frequencySec={frequencySec}
            onBudgetChange={setTotalAmountQuote}
            onFrequencyChange={setFrequencySec}
          />

          <DigestIntervalField
            value={digestIntervalTicks}
            onChange={setDigestIntervalTicks}
            defaultHint={Number(agentConfig.digest_interval_ticks ?? 0)}
            executionMode={executionMode}
          />

          <RiskLimitsFields
            totalAmountQuote={totalAmountQuote}
            maxOpenExecutors={maxOpenExecutors}
            maxDrawdown={maxDrawdown}
            onMaxOpenExecutorsChange={setMaxOpenExecutors}
            onMaxDrawdownChange={setMaxDrawdown}
          />

          {strategySchema && Object.keys(strategySchema.fields).length > 0 && (
            <StrategyParamsForm
              fields={strategySchema.fields}
              groups={strategySchema.groups}
              values={strategyParams}
              frequencySec={Number(frequencySec) || Number(agentConfig.frequency_sec) || 60}
              onChange={handleStrategyParamChange}
            />
          )}
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
              executionMode === "dry_run"
                ? "bg-blue-600 hover:bg-blue-500"
                : executionMode === "run_once"
                  ? "bg-amber-600 hover:bg-amber-500"
                  : "bg-emerald-600 hover:bg-emerald-500"
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

// ── Agent Controls ──

export function AgentControls({
  slug,
  status,
  defaultContext,
  agentConfig,
}: {
  slug: string;
  status: string;
  defaultContext: string;
  agentConfig: Record<string, unknown>;
}) {
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
