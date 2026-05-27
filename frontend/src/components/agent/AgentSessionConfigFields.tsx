import { useQuery } from "@tanstack/react-query";
import { Clock, MessageSquareText, Server, Zap } from "lucide-react";

import { api } from "@/lib/api";

export type ExecutionMode = "dry_run" | "run_once" | "loop";

export const inputClass =
  "w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]";

export const labelClass =
  "mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]";

export const fieldHintClass = "mb-1 block text-[10px] text-[var(--color-text-muted)]";

export function computeMaxTotalExposure(budget: number, maxExecutors: number): number {
  const b = Number.isFinite(budget) ? budget : 0;
  const m = Number.isFinite(maxExecutors) ? maxExecutors : 0;
  return b * m;
}

export function buildRiskLimitsPayload(maxOpenExecutors: string, maxDrawdown: string) {
  return {
    max_open_executors: Number(maxOpenExecutors) || 5,
    max_drawdown_pct: Number(maxDrawdown),
  };
}

export function ExecutionModePicker({
  value,
  onChange,
}: {
  value: ExecutionMode;
  onChange: (mode: ExecutionMode) => void;
}) {
  return (
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
            onClick={() => onChange(opt.value)}
            className={`flex-1 rounded-md px-3 py-2 text-center text-xs font-medium transition-all ${
              value === opt.value
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
  );
}

export function ServerSelect({
  value,
  onChange,
  enabled = true,
}: {
  value: string;
  onChange: (value: string) => void;
  enabled?: boolean;
}) {
  const { data: servers } = useQuery({
    queryKey: ["servers"],
    queryFn: () => api.getServers(),
    enabled,
  });

  return (
    <div>
      <label className={labelClass}>
        <Server className="h-3.5 w-3.5" />
        Server
      </label>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={inputClass}>
        <option value="">Auto (current default)</option>
        {servers?.map((s) => (
          <option key={s.name} value={s.name} disabled={!s.online}>
            {s.name} {s.online ? "" : "(offline)"}
          </option>
        ))}
      </select>
    </div>
  );
}

export function BudgetFrequencyFields({
  executionMode,
  totalAmountQuote,
  frequencySec,
  onBudgetChange,
  onFrequencyChange,
}: {
  executionMode: ExecutionMode;
  totalAmountQuote: string;
  frequencySec: string;
  onBudgetChange: (value: string) => void;
  onFrequencyChange: (value: string) => void;
}) {
  return (
    <div className={`grid gap-4 ${executionMode === "loop" ? "grid-cols-2" : "grid-cols-1"}`}>
      <div>
        <label className={labelClass}>Budget (USDT)</label>
        <input
          type="number"
          min={1}
          step={10}
          value={totalAmountQuote}
          onChange={(e) => onBudgetChange(e.target.value)}
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
            onChange={(e) => onFrequencyChange(e.target.value)}
            className={inputClass}
          />
        </div>
      )}
    </div>
  );
}

export function DigestIntervalField({
  value,
  onChange,
  defaultHint,
  showAlways = false,
  executionMode = "loop",
}: {
  value: string;
  onChange: (value: string) => void;
  defaultHint?: string | number;
  showAlways?: boolean;
  executionMode?: ExecutionMode;
}) {
  if (!showAlways && executionMode !== "loop") return null;

  return (
    <div>
      <label className={labelClass}>Digest interval (ticks)</label>
      <p className="mb-2 text-xs text-[var(--color-text-muted)]">
        Telegram summary every N hold-only ticks. 0 = off.
        {defaultHint !== undefined && (
          <> Default from strategy is {String(defaultHint)}.</>
        )}
      </p>
      <input
        type="number"
        min={0}
        step={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={inputClass}
      />
    </div>
  );
}

export function MaxTicksField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label className={labelClass}>Max ticks</label>
      <p className="mb-2 text-xs text-[var(--color-text-muted)]">
        Auto-stop after N ticks. 0 = unlimited.
      </p>
      <input
        type="number"
        min={0}
        step={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={inputClass}
      />
    </div>
  );
}

export function ModelFields({
  agentKey,
  modelBaseUrl,
  onAgentKeyChange,
  onModelBaseUrlChange,
}: {
  agentKey: string;
  modelBaseUrl: string;
  onAgentKeyChange: (value: string) => void;
  onModelBaseUrlChange: (value: string) => void;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div>
        <label className={labelClass}>Model</label>
        <input
          type="text"
          value={agentKey}
          onChange={(e) => onAgentKeyChange(e.target.value)}
          placeholder="e.g. cursor:composer-2"
          className={inputClass}
        />
      </div>
      <div>
        <label className={labelClass}>Model base URL</label>
        <input
          type="text"
          value={modelBaseUrl}
          onChange={(e) => onModelBaseUrlChange(e.target.value)}
          placeholder="Optional OpenAI-compatible endpoint"
          className={inputClass}
        />
      </div>
    </div>
  );
}

export function TradingContextField({
  value,
  onChange,
  label = "Trading Context",
  description = "Describe what this session should focus on. This guides the agent's trading decisions.",
  rows = 3,
  autoFocus = false,
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  description?: string;
  rows?: number;
  autoFocus?: boolean;
}) {
  return (
    <div>
      <label className={labelClass}>
        <MessageSquareText className="h-3.5 w-3.5" />
        {label}
      </label>
      <p className="mb-2 text-xs text-[var(--color-text-muted)]">{description}</p>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. Focus on SOL meme coins, ride momentum for 5-10% gains, tight stops at 3%..."
        rows={rows}
        className={`${inputClass} resize-none`}
        autoFocus={autoFocus}
      />
    </div>
  );
}

export function RiskLimitsFields({
  totalAmountQuote,
  maxOpenExecutors,
  maxDrawdown,
  onMaxOpenExecutorsChange,
  onMaxDrawdownChange,
}: {
  totalAmountQuote: string;
  maxOpenExecutors: string;
  maxDrawdown: string;
  onMaxOpenExecutorsChange: (value: string) => void;
  onMaxDrawdownChange: (value: string) => void;
}) {
  const maxTotalExposure = computeMaxTotalExposure(
    Number(totalAmountQuote) || 0,
    Number(maxOpenExecutors) || 0,
  );

  return (
    <div>
      <label className={`${labelClass} mb-3`}>
        <Zap className="h-3.5 w-3.5" />
        Risk Limits
      </label>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <span className={fieldHintClass}>Max Total Exposure (USDT)</span>
          <div className={`${inputClass} bg-[var(--color-bg)] text-[var(--color-text-muted)]`}>
            ${maxTotalExposure.toFixed(0)}
          </div>
        </div>
        <div>
          <span className={fieldHintClass}>Max Executors</span>
          <input
            type="number"
            min={1}
            value={maxOpenExecutors}
            onChange={(e) => onMaxOpenExecutorsChange(e.target.value)}
            className={inputClass}
          />
        </div>
        <div>
          <span className={fieldHintClass}>Max Drawdown %</span>
          <input
            type="number"
            value={maxDrawdown}
            onChange={(e) => onMaxDrawdownChange(e.target.value)}
            className={inputClass}
          />
        </div>
      </div>
    </div>
  );
}
