import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Settings, X } from "lucide-react";
import { useEffect, useState } from "react";

import {
  BudgetFrequencyFields,
  DigestIntervalField,
  ExecutionModePicker,
  MaxTicksField,
  ModelFields,
  RiskLimitsFields,
  ServerSelect,
  TradingContextField,
  buildRiskLimitsPayload,
  type ExecutionMode,
} from "@/components/agent/AgentSessionConfigFields";
import { api } from "@/lib/api";

function readRiskLimit(defaults: Record<string, unknown>, key: string, fallback: number) {
  const risk = (defaults.risk_limits || {}) as Record<string, unknown>;
  const value = risk[key];
  return value !== undefined && value !== null ? String(value) : String(fallback);
}

export function AgentDefaultsDialog({
  open,
  onClose,
  slug,
}: {
  open: boolean;
  onClose: () => void;
  slug: string;
}) {
  const queryClient = useQueryClient();

  const { data: defaults, isLoading } = useQuery({
    queryKey: ["agent-defaults", slug],
    queryFn: () => api.getAgentDefaults(slug),
    enabled: open,
  });

  const [executionMode, setExecutionMode] = useState<ExecutionMode>("loop");
  const [defaultTradingContext, setDefaultTradingContext] = useState("");
  const [serverName, setServerName] = useState("");
  const [agentKey, setAgentKey] = useState("");
  const [modelBaseUrl, setModelBaseUrl] = useState("");
  const [totalAmountQuote, setTotalAmountQuote] = useState("100");
  const [frequencySec, setFrequencySec] = useState("60");
  const [digestIntervalTicks, setDigestIntervalTicks] = useState("0");
  const [maxTicks, setMaxTicks] = useState("0");
  const [maxOpenExecutors, setMaxOpenExecutors] = useState("5");
  const [maxDrawdown, setMaxDrawdown] = useState("-1");

  useEffect(() => {
    if (!defaults) return;
    const cfg = defaults.default_config;
    setExecutionMode((cfg.execution_mode as ExecutionMode) || "loop");
    setDefaultTradingContext(defaults.default_trading_context || "");
    setServerName((cfg.server_name as string) || "");
    setAgentKey(defaults.agent_key || "");
    setModelBaseUrl(defaults.model_base_url || (cfg.model_base_url as string) || "");
    setTotalAmountQuote(String(cfg.total_amount_quote ?? 100));
    setFrequencySec(String(cfg.frequency_sec ?? 60));
    setDigestIntervalTicks(String(cfg.digest_interval_ticks ?? 0));
    setMaxTicks(String(cfg.max_ticks ?? 0));
    setMaxOpenExecutors(readRiskLimit(cfg, "max_open_executors", 5));
    setMaxDrawdown(readRiskLimit(cfg, "max_drawdown_pct", -1));
  }, [defaults]);

  const saveMut = useMutation({
    mutationFn: () =>
      api.updateAgentDefaults(slug, {
        default_config: {
          server_name: serverName,
          total_amount_quote: Number(totalAmountQuote) || 100,
          frequency_sec: Number(frequencySec) || 60,
          digest_interval_ticks: Math.max(0, Number(digestIntervalTicks) || 0),
          execution_mode: executionMode,
          max_ticks: Math.max(0, Number(maxTicks) || 0),
          risk_limits: buildRiskLimitsPayload(maxOpenExecutors, maxDrawdown),
        },
        default_trading_context: defaultTradingContext,
        agent_key: agentKey,
        model_base_url: modelBaseUrl,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      queryClient.invalidateQueries({ queryKey: ["agent-defaults", slug] });
      onClose();
    },
  });

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-[var(--color-text)]">
            <Settings className="h-5 w-5" />
            Session Defaults
          </h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <X className="h-4 w-4" />
          </button>
        </div>

        {isLoading ? (
          <div className="flex h-40 items-center justify-center text-[var(--color-text-muted)]">
            Loading defaults...
          </div>
        ) : (
          <div className="space-y-5">
            <ModelFields
              agentKey={agentKey}
              modelBaseUrl={modelBaseUrl}
              onAgentKeyChange={setAgentKey}
              onModelBaseUrlChange={setModelBaseUrl}
            />

            <ExecutionModePicker value={executionMode} onChange={setExecutionMode} />

            <TradingContextField
              value={defaultTradingContext}
              onChange={setDefaultTradingContext}
              label="Default Trading Context"
              description="Pre-filled when starting a new session. Override per session in the Start dialog."
            />

            <ServerSelect value={serverName} onChange={setServerName} enabled={open} />

            <BudgetFrequencyFields
              executionMode={executionMode}
              totalAmountQuote={totalAmountQuote}
              frequencySec={frequencySec}
              onBudgetChange={setTotalAmountQuote}
              onFrequencyChange={setFrequencySec}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <DigestIntervalField
                value={digestIntervalTicks}
                onChange={setDigestIntervalTicks}
                showAlways
              />
              <MaxTicksField value={maxTicks} onChange={setMaxTicks} />
            </div>

            <RiskLimitsFields
              totalAmountQuote={totalAmountQuote}
              maxOpenExecutors={maxOpenExecutors}
              maxDrawdown={maxDrawdown}
              onMaxOpenExecutorsChange={setMaxOpenExecutors}
              onMaxDrawdownChange={setMaxDrawdown}
            />
          </div>
        )}

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <button
            onClick={() => saveMut.mutate()}
            disabled={saveMut.isPending || isLoading}
            className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-all disabled:opacity-40"
          >
            {saveMut.isPending ? "Saving..." : "Save Defaults"}
          </button>
        </div>

        {saveMut.isError && (
          <p className="mt-3 text-xs text-red-400">Failed to save defaults. Please try again.</p>
        )}
      </div>
    </div>
  );
}
