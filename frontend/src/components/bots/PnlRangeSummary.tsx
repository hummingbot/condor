import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DollarSign, TrendingUp, Volume2, Receipt } from "lucide-react";

import { api } from "@/lib/api";
import { formatCurrencyVolume, formatCurrencyPnl, pnlColor } from "@/lib/formatters";

type Period = "today" | "7d" | "30d";

const PERIOD_LABELS: Record<Period, string> = {
  today: "Today",
  "7d": "7D",
  "30d": "30D",
};

/**
 * Cumulative realized PnL/volume/fees over a selectable time window, sourced
 * directly from Hyperliquid's own fill history rather than any specific bot
 * instance's performance bookkeeping. Sits alongside the per-run chart above it
 * (which resets its view on every redeploy) so this stays accurate regardless of
 * how many times the bots have been restarted in the selected window.
 *
 * "Today" resets at midnight UTC, not local time or bot-deploy time.
 */
export function PnlRangeSummary({
  server,
  coins,
  currencySymbol,
}: {
  server: string;
  coins: string[];
  currencySymbol: string;
}) {
  const [period, setPeriod] = useState<Period>("today");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["pnl-summary", server, period, coins.join(",")],
    queryFn: () => api.getPnlSummary(server, period, coins),
    enabled: !!server && coins.length > 0,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const total = data?.total;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Cumulative PnL{" "}
          <span className="font-normal text-[var(--color-text-muted)]">
            (from real fills — survives restarts/redeploys)
          </span>
        </h3>
        <div className="flex items-center gap-1 rounded-md border border-[var(--color-border)] p-0.5">
          {(Object.keys(PERIOD_LABELS) as Period[]).map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                period === p
                  ? "bg-[var(--color-accent)] text-white"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {PERIOD_LABELS[p]}
            </button>
          ))}
        </div>
      </div>

      {isError ? (
        <p className="text-xs text-[var(--color-red)]">
          Failed to load fill-based PnL summary. Is HYPERLIQUID_ADDRESS configured?
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MiniStat
            label="Realized PnL"
            value={total ? formatCurrencyPnl(total.realized_pnl, currencySymbol) : "—"}
            color={total ? pnlColor(total.realized_pnl) : undefined}
            icon={TrendingUp}
            loading={isLoading}
          />
          <MiniStat
            label="Volume"
            value={total ? formatCurrencyVolume(total.volume, currencySymbol) : "—"}
            icon={Volume2}
            loading={isLoading}
          />
          <MiniStat
            label="Fees"
            value={total ? formatCurrencyVolume(total.fees, currencySymbol) : "—"}
            icon={Receipt}
            loading={isLoading}
          />
          <MiniStat
            label="Trades"
            value={total ? String(total.trade_count) : "—"}
            icon={DollarSign}
            loading={isLoading}
          />
        </div>
      )}
    </div>
  );
}

function MiniStat({
  label,
  value,
  color,
  icon: Icon,
  loading,
}: {
  label: string;
  value: string;
  color?: string;
  icon: React.ComponentType<{ className?: string }>;
  loading?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <Icon className="h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />
      <div>
        <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          {label}
        </p>
        <p
          className={`text-sm font-bold tabular-nums ${loading ? "opacity-50" : ""}`}
          style={color ? { color } : {}}
        >
          {value}
        </p>
      </div>
    </div>
  );
}
