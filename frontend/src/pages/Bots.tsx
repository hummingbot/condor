import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Circle,
  Layers,
  TrendingUp,
  Volume2,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import { useServer } from "@/hooks/useServer";
import { api, type BotSummary, type ControllerInfo } from "@/lib/api";

// ── Formatters ──

function formatUsd(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return val.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

function formatVolume(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(1) + "M";
  if (Math.abs(val) >= 1_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return "$" + val.toFixed(0);
}

function formatPnl(val: number) {
  const prefix = val >= 0 ? "+" : "";
  return prefix + formatUsd(val);
}

function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

function formatUptime(deployedAt: string | null): string {
  if (!deployedAt) return "—";
  try {
    const deployed = new Date(deployedAt);
    const now = new Date();
    const diffMs = now.getTime() - deployed.getTime();
    if (diffMs < 0) return "—";
    const days = Math.floor(diffMs / 86400000);
    const hours = Math.floor((diffMs % 86400000) / 3600000);
    if (days > 0) return `${days}d ${hours}h`;
    const mins = Math.floor((diffMs % 3600000) / 60000);
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins}m`;
  } catch {
    return "—";
  }
}

// ── Sort types ──

type SortKey =
  | "controller_name"
  | "connector"
  | "trading_pair"
  | "realized_pnl_quote"
  | "unrealized_pnl_quote"
  | "global_pnl_quote"
  | "volume_traded"
  | "deployed_at"
  | "status";

type SortDir = "asc" | "desc";

function compareControllers(a: ControllerInfo, b: ControllerInfo, key: SortKey, dir: SortDir): number {
  let cmp = 0;
  switch (key) {
    case "controller_name":
    case "connector":
    case "trading_pair":
    case "status":
      cmp = (a[key] || "").localeCompare(b[key] || "");
      break;
    case "realized_pnl_quote":
    case "unrealized_pnl_quote":
    case "global_pnl_quote":
    case "volume_traded":
      cmp = a[key] - b[key];
      break;
    case "deployed_at": {
      const aTime = a.deployed_at ? new Date(a.deployed_at).getTime() : 0;
      const bTime = b.deployed_at ? new Date(b.deployed_at).getTime() : 0;
      cmp = aTime - bTime;
      break;
    }
  }
  return dir === "asc" ? cmp : -cmp;
}

// ── Stat Card ──

function StatCard({
  label,
  value,
  icon: Icon,
  valueColor,
}: {
  label: string;
  value: string;
  icon: React.ComponentType<{ className?: string }>;
  valueColor?: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">
          {label}
        </span>
      </div>
      <p
        className="text-xl font-bold tabular-nums"
        style={valueColor ? { color: valueColor } : {}}
      >
        {value}
      </p>
    </div>
  );
}

// ── Status Dot ──

function StatusDot({ status }: { status: string }) {
  const color =
    status === "running"
      ? "text-[var(--color-green)]"
      : status === "stopped" || status === "error"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-yellow)]";
  return <Circle className={`h-2 w-2 fill-current ${color}`} />;
}

// ── Sortable Header ──

function SortHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  onSort: (key: SortKey) => void;
  align?: "left" | "right" | "center";
}) {
  const active = currentKey === sortKey;
  const alignCls =
    align === "right" ? "text-right justify-end" : align === "center" ? "text-center justify-center" : "text-left";

  return (
    <th
      className={`px-4 py-3 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] cursor-pointer select-none hover:text-[var(--color-text)] transition-colors ${alignCls}`}
      onClick={() => onSort(sortKey)}
    >
      <div className={`flex items-center gap-1 ${align === "right" ? "justify-end" : align === "center" ? "justify-center" : ""}`}>
        {label}
        {active ? (
          currentDir === "asc" ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )
        ) : (
          <span className="w-3" />
        )}
      </div>
    </th>
  );
}

// ── Side Panel ──

function DetailPanel({
  ctrl,
  onClose,
}: {
  ctrl: ControllerInfo;
  onClose: () => void;
}) {
  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/30 z-40" onClick={onClose} />
      {/* Panel */}
      <div className="fixed top-0 right-0 h-full w-[400px] max-w-[90vw] bg-[var(--color-bg)] border-l border-[var(--color-border)] z-50 overflow-y-auto shadow-xl">
        <div className="sticky top-0 bg-[var(--color-bg)] border-b border-[var(--color-border)] px-5 py-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold truncate pr-4" title={ctrl.controller_name}>
            {ctrl.controller_name}
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-surface-hover)] transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-5 space-y-5">
          {/* Status & Meta */}
          <div className="flex items-center gap-4 text-sm">
            <div className="flex items-center gap-1.5">
              <StatusDot status={ctrl.status} />
              <span className="capitalize">{ctrl.status}</span>
            </div>
            {ctrl.connector && (
              <span className="text-[var(--color-text-muted)]">{ctrl.connector}</span>
            )}
            {ctrl.trading_pair && (
              <span className="text-[var(--color-text-muted)]">{ctrl.trading_pair}</span>
            )}
          </div>

          {/* PnL Breakdown */}
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-3">
            <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              PnL Breakdown
            </h3>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Realized</div>
                <div className="font-medium tabular-nums" style={{ color: pnlColor(ctrl.realized_pnl_quote) }}>
                  {formatPnl(ctrl.realized_pnl_quote)}
                </div>
              </div>
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Unrealized</div>
                <div className="font-medium tabular-nums" style={{ color: pnlColor(ctrl.unrealized_pnl_quote) }}>
                  {formatPnl(ctrl.unrealized_pnl_quote)}
                </div>
              </div>
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Total PnL</div>
                <div className="font-medium tabular-nums" style={{ color: pnlColor(ctrl.global_pnl_quote) }}>
                  {formatPnl(ctrl.global_pnl_quote)}
                </div>
              </div>
              {ctrl.global_pnl_pct !== 0 && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">PnL %</div>
                  <div className="font-medium tabular-nums" style={{ color: pnlColor(ctrl.global_pnl_pct) }}>
                    {ctrl.global_pnl_pct >= 0 ? "+" : ""}
                    {ctrl.global_pnl_pct.toFixed(2)}%
                  </div>
                </div>
              )}
            </div>
            <div className="pt-2 border-t border-[var(--color-border)]/50 text-sm">
              <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Volume Traded</div>
              <div className="font-medium tabular-nums">{formatVolume(ctrl.volume_traded)}</div>
            </div>
          </div>

          {/* Close Type Counts */}
          {Object.keys(ctrl.close_type_counts).length > 0 && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-2">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Close Types
              </h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(ctrl.close_type_counts).map(([type, count]) => (
                  <span
                    key={type}
                    className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-bg)] px-2.5 py-1 text-xs border border-[var(--color-border)]/50"
                  >
                    <span className="text-[var(--color-text-muted)]">{type}</span>
                    <span className="font-semibold">{count}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Positions Summary */}
          {ctrl.positions_summary.length > 0 && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-2">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Positions
              </h3>
              <div className="overflow-x-auto">
                <table className="text-xs w-full">
                  <tbody>
                    {ctrl.positions_summary.map((pos, i) => (
                      <tr key={i} className="border-b border-[var(--color-border)]/30 last:border-0">
                        {Object.entries(pos).map(([key, val]) => (
                          <td key={key} className="pr-3 py-1.5">
                            <span className="text-[var(--color-text-muted)]">{key}: </span>
                            <span className="tabular-nums">
                              {typeof val === "number" ? val.toFixed(4) : String(val)}
                            </span>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ── Bots Collapsible Section ──

function BotsSection({ bots }: { bots: BotSummary[] }) {
  const [expanded, setExpanded] = useState(false);
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-[var(--color-surface-hover)] transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <Chevron className="h-4 w-4 text-[var(--color-text-muted)]" />
        <span className="font-medium">Bots</span>
        <span className="text-sm text-[var(--color-text-muted)]">({bots.length})</span>
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)] divide-y divide-[var(--color-border)]/30">
          {bots.map((bot) => (
            <div key={bot.bot_name} className="flex items-center gap-4 px-4 py-2.5 text-sm">
              <StatusDot status={bot.status} />
              <span className="font-medium truncate max-w-[250px]" title={bot.bot_name}>
                {bot.bot_name}
              </span>
              <span className="text-[var(--color-text-muted)]">
                {bot.num_controllers} controller{bot.num_controllers !== 1 ? "s" : ""}
              </span>
              {bot.error_count > 0 && (
                <span className="text-[var(--color-yellow)] text-xs">
                  {bot.error_count} error{bot.error_count !== 1 ? "s" : ""}
                </span>
              )}
              <span className="ml-auto text-[var(--color-text-muted)] tabular-nums">
                {formatUptime(bot.deployed_at)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Page ──

export function Bots() {
  const { server } = useServer();
  const [sortKey, setSortKey] = useState<SortKey>("global_pnl_quote");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedController, setSelectedController] = useState<ControllerInfo | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["bots", server],
    queryFn: () => api.getBots(server!),
    enabled: !!server,
    refetchInterval: 10000,
  });

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const controllers = data?.controllers ?? [];
  const bots = data?.bots ?? [];

  const sortedControllers = useMemo(
    () => [...controllers].sort((a, b) => compareControllers(a, b, sortKey, sortDir)),
    [controllers, sortKey, sortDir],
  );

  if (!server) {
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;
  }
  if (isLoading) return <p className="text-[var(--color-text-muted)]">Loading...</p>;
  if (error)
    return (
      <p className="text-[var(--color-red)]">
        {error instanceof Error ? error.message : "Error"}
      </p>
    );

  const serverOnline = data?.server_online !== false;
  const errorHint = data?.error_hint;
  const totalPnl = data?.total_pnl ?? 0;
  const totalVolume = data?.total_volume ?? 0;
  const activeBots = bots.filter((b) => b.status === "running").length;

  const isEmpty = controllers.length === 0 && bots.length === 0;

  if (!serverOnline) {
    return (
      <div className="space-y-6">
        <div className="rounded-lg border border-[var(--color-yellow)]/40 bg-[var(--color-yellow)]/10 px-4 py-3">
          <p className="text-sm font-medium text-[var(--color-yellow)]">
            Unable to reach server
          </p>
          {errorHint && (
            <p className="text-xs text-[var(--color-text-muted)] mt-1">{errorHint}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Summary stat cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Total PnL"
          value={formatPnl(totalPnl)}
          icon={TrendingUp}
          valueColor={pnlColor(totalPnl)}
        />
        <StatCard label="Volume" value={formatVolume(totalVolume)} icon={Volume2} />
        <StatCard label="Active Bots" value={String(activeBots)} icon={Bot} />
        <StatCard label="Controllers" value={String(controllers.length)} icon={Layers} />
      </div>

      {isEmpty ? (
        <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
          <Bot className="h-10 w-10" />
          <p>No bots running</p>
        </div>
      ) : (
        <>
          {/* Controllers table */}
          {controllers.length > 0 && (
            <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
                      <SortHeader label="Controller" sortKey="controller_name" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
                      <SortHeader label="Connector" sortKey="connector" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
                      <SortHeader label="Pair" sortKey="trading_pair" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
                      <SortHeader label="Realized" sortKey="realized_pnl_quote" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="right" />
                      <SortHeader label="Unrealized" sortKey="unrealized_pnl_quote" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="right" />
                      <SortHeader label="Total PnL" sortKey="global_pnl_quote" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="right" />
                      <SortHeader label="Volume" sortKey="volume_traded" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="right" />
                      <SortHeader label="Age" sortKey="deployed_at" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="right" />
                      <SortHeader label="Status" sortKey="status" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="center" />
                    </tr>
                  </thead>
                  <tbody>
                    {sortedControllers.map((ctrl) => {
                      const isSelected =
                        selectedController?.controller_name === ctrl.controller_name &&
                        selectedController?.bot_name === ctrl.bot_name;
                      return (
                        <tr
                          key={`${ctrl.bot_name}-${ctrl.controller_name}`}
                          className={`border-b border-[var(--color-border)]/30 hover:bg-[var(--color-surface-hover)]/50 cursor-pointer transition-colors ${isSelected ? "bg-[var(--color-surface-hover)]/70" : ""}`}
                          onClick={() => setSelectedController(ctrl)}
                        >
                          <td className="px-4 py-2.5">
                            <span className="text-sm font-medium" title={ctrl.controller_name}>
                              {ctrl.controller_name}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-sm text-[var(--color-text-muted)]">
                            {ctrl.connector || "—"}
                          </td>
                          <td className="px-4 py-2.5 text-sm">{ctrl.trading_pair || "—"}</td>
                          <td
                            className="px-4 py-2.5 text-sm text-right tabular-nums font-medium"
                            style={{ color: pnlColor(ctrl.realized_pnl_quote) }}
                          >
                            {formatPnl(ctrl.realized_pnl_quote)}
                          </td>
                          <td
                            className="px-4 py-2.5 text-sm text-right tabular-nums font-medium"
                            style={{ color: pnlColor(ctrl.unrealized_pnl_quote) }}
                          >
                            {formatPnl(ctrl.unrealized_pnl_quote)}
                          </td>
                          <td
                            className="px-4 py-2.5 text-sm text-right tabular-nums font-medium"
                            style={{ color: pnlColor(ctrl.global_pnl_quote) }}
                          >
                            {formatPnl(ctrl.global_pnl_quote)}
                          </td>
                          <td className="px-4 py-2.5 text-sm text-right tabular-nums text-[var(--color-text-muted)]">
                            {formatVolume(ctrl.volume_traded)}
                          </td>
                          <td className="px-4 py-2.5 text-sm text-right tabular-nums text-[var(--color-text-muted)]">
                            {formatUptime(ctrl.deployed_at)}
                          </td>
                          <td className="px-4 py-2.5">
                            <div className="flex items-center gap-1.5 justify-center">
                              <StatusDot status={ctrl.status} />
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Bots collapsible section */}
          {bots.length > 0 && <BotsSection bots={bots} />}
        </>
      )}

      {/* Side panel */}
      {selectedController && (
        <DetailPanel ctrl={selectedController} onClose={() => setSelectedController(null)} />
      )}
    </div>
  );
}
