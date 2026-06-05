import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { PnlSparkline } from "@/components/bots/PnlSparkline";
import { api } from "@/lib/api";

interface Props {
  server: string;
  controllerId: string;
  botName: string;
}

/** Fields to always skip (not useful for evolution tracking) */
const SKIP_FIELDS = new Set([
  "controller_id",
  "controller_name",
  "bot_name",
  "connector",
  "connector_name",
  "trading_pair",
  "timestamp",
]);

interface FieldEvolution {
  key: string;
  values: number[];
  current: number;
  min: number;
  max: number;
  change: number; // last - first
}

export function CustomInfoEvolution({ server, controllerId, botName }: Props) {
  const [expandedField, setExpandedField] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["controller-perf-history", server, controllerId],
    queryFn: () =>
      api.getControllerPerformanceHistory(server, {
        controller_id: controllerId,
        bot_name: botName,
        interval: "5m",
        limit: 2000,
      }),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const fields = useMemo(() => {
    const snapshots = data?.snapshots;
    if (!snapshots || snapshots.length === 0) return [];

    const sorted = [...snapshots].sort(
      (a, b) => (Date.parse(a.timestamp) || 0) - (Date.parse(b.timestamp) || 0),
    );

    // Collect all numeric fields from custom_info across snapshots
    // fieldMap: key -> (snapshotIndex -> value)
    const fieldMap = new Map<string, (number | undefined)[]>();
    const n = sorted.length;

    for (let i = 0; i < n; i++) {
      const info = sorted[i].custom_info;
      if (!info || typeof info !== "object") continue;
      extractNumericFields(info, "", (key, val) => {
        if (!fieldMap.has(key)) fieldMap.set(key, new Array(n).fill(undefined));
        fieldMap.get(key)![i] = val;
      });
    }

    const result: FieldEvolution[] = [];
    for (const [key, rawValues] of fieldMap) {
      if (SKIP_FIELDS.has(key)) continue;
      const defined = rawValues.filter((v): v is number => v !== undefined);
      if (defined.length < 2) continue;

      // Fill gaps with last known value
      const filled: number[] = [];
      let lastKnown = 0;
      for (const v of rawValues) {
        if (v !== undefined) lastKnown = v;
        filled.push(lastKnown);
      }

      const min = Math.min(...filled);
      const max = Math.max(...filled);
      if (min === max) continue;

      result.push({
        key,
        values: filled,
        current: filled[filled.length - 1],
        min,
        max,
        change: filled[filled.length - 1] - filled[0],
      });
    }

    // Sort by absolute change descending (most interesting first)
    result.sort((a, b) => Math.abs(b.change) - Math.abs(a.change));
    return result;
  }, [data]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-32">
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          Loading...
        </div>
      </div>
    );
  }

  if (fields.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-32 text-[var(--color-text-muted)]">
        <p className="text-xs">No custom info evolution data</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--color-border)]/50">
        <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Custom Info Evolution
        </h3>
        <span className="text-[10px] text-[var(--color-text-muted)]">
          {fields.length} fields
        </span>
      </div>
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {fields.map((field) => {
          const isExpanded = expandedField === field.key;
          const changeColor = field.change >= 0 ? "var(--color-green)" : "var(--color-red)";
          const changeSign = field.change >= 0 ? "+" : "";

          return (
            <div
              key={field.key}
              className="border-b border-[var(--color-border)]/20 last:border-b-0"
            >
              <button
                onClick={() => setExpandedField(isExpanded ? null : field.key)}
                className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--color-surface-hover)]/50 transition-colors"
              >
                <div className="flex-1 min-w-0 text-left">
                  <span className="text-[11px] font-medium text-[var(--color-text)] truncate block">
                    {field.key}
                  </span>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[10px] text-[var(--color-text-muted)] tabular-nums">
                      {formatCompact(field.current)}
                    </span>
                    <span
                      className="text-[10px] font-medium tabular-nums"
                      style={{ color: changeColor }}
                    >
                      {changeSign}{formatCompact(field.change)}
                    </span>
                  </div>
                </div>
                <PnlSparkline values={field.values} width={64} height={20} />
              </button>

              {isExpanded && (
                <div className="px-4 pb-3 pt-1">
                  <div className="grid grid-cols-3 gap-2 text-[10px]">
                    <div>
                      <span className="text-[var(--color-text-muted)]">Min</span>
                      <div className="font-mono tabular-nums">{formatCompact(field.min)}</div>
                    </div>
                    <div>
                      <span className="text-[var(--color-text-muted)]">Max</span>
                      <div className="font-mono tabular-nums">{formatCompact(field.max)}</div>
                    </div>
                    <div>
                      <span className="text-[var(--color-text-muted)]">Current</span>
                      <div className="font-mono tabular-nums">{formatCompact(field.current)}</div>
                    </div>
                  </div>
                  {/* Larger sparkline when expanded */}
                  <div className="mt-2 rounded border border-[var(--color-border)]/30 bg-[var(--color-bg)] p-2">
                    <PnlSparkline values={field.values} width={280} height={48} />
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatCompact(val: number): string {
  if (Math.abs(val) >= 1_000_000) return (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 1_000) return (val / 1_000).toFixed(1) + "K";
  if (Math.abs(val) >= 1) return val.toFixed(2);
  if (Math.abs(val) >= 0.001) return val.toFixed(4);
  if (val === 0) return "0";
  return val.toExponential(2);
}

function extractNumericFields(
  obj: Record<string, unknown>,
  prefix: string,
  emit: (key: string, val: number) => void,
) {
  for (const [key, val] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof val === "number" && isFinite(val)) {
      emit(fullKey, val);
    } else if (val && typeof val === "object" && !Array.isArray(val)) {
      extractNumericFields(val as Record<string, unknown>, fullKey, emit);
    }
  }
}
