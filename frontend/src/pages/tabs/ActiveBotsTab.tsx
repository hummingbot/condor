import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Circle,
  Layers,
  Pause,
  Play,
  Rocket,
  Square,
  TrendingUp,
  Volume2,
} from "lucide-react";
import { useMemo, useState } from "react";

import { ControllerBrowser } from "@/components/bots/ControllerBrowser";
import { DeployBotDialog } from "@/components/bots/DeployBotDialog";

import { useServer } from "@/hooks/useServer";
import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { api, type BotLogEntry, type BotSummary, type ControllerInfo } from "@/lib/api";

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

function formatLogTime(ts?: number): string {
  if (!ts) return "";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

function LogsSection({ logs }: { logs: BotLogEntry[] }) {
  const [filter, setFilter] = useState<"all" | "error" | "general">("all");
  const filtered = filter === "all" ? logs : logs.filter((l) => l.log_category === filter);

  if (logs.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">No logs available</p>
    );
  }

  const errorCount = logs.filter((l) => l.log_category === "error").length;
  const generalCount = logs.filter((l) => l.log_category === "general").length;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        {(["all", "general", "error"] as const).map((f) => {
          const count = f === "all" ? logs.length : f === "error" ? errorCount : generalCount;
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                filter === f
                  ? f === "error"
                    ? "bg-[var(--color-red)]/15 text-[var(--color-red)]"
                    : "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {f} ({count})
            </button>
          );
        })}
      </div>
      <div className="max-h-[300px] overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] font-mono text-[11px] leading-relaxed">
        {filtered.map((log, i) => (
          <div
            key={i}
            className={`flex gap-2 px-2.5 py-1 border-b border-[var(--color-border)]/20 last:border-b-0 ${
              log.log_category === "error" ? "bg-[var(--color-red)]/5" : ""
            }`}
          >
            <span className="text-[var(--color-text-muted)] shrink-0 tabular-nums">
              {formatLogTime(log.timestamp)}
            </span>
            <span
              className={`break-all ${
                log.log_category === "error" ? "text-[var(--color-red)]" : "text-[var(--color-text)]"
              }`}
            >
              {log.msg || JSON.stringify(log)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Controller Row with actions ──

function ControllerRow({
  ctrl,
  server,
  isSelected,
  onSelect,
}: {
  ctrl: ControllerInfo;
  server: string;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const queryClient = useQueryClient();
  const isKilled = ctrl.config?.manual_kill_switch === true;

  const toggleMutation = useMutation({
    mutationFn: () =>
      isKilled
        ? api.startControllers(server, ctrl.bot_name, [ctrl.controller_name])
        : api.stopControllers(server, ctrl.bot_name, [ctrl.controller_name]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bots", server] });
    },
  });

  return (
    <tr
      className={`border-b border-[var(--color-border)]/30 hover:bg-[var(--color-surface-hover)]/50 cursor-pointer transition-colors ${isSelected ? "bg-[var(--color-surface-hover)]/70" : ""}`}
      onClick={onSelect}
    >
      <td className="px-4 py-2.5">
        <div className="flex flex-col">
          <span className="text-sm font-medium" title={ctrl.controller_name}>
            {ctrl.controller_name}
          </span>
          {ctrl.controller_id && ctrl.controller_id !== ctrl.controller_name && (
            <span className="text-xs text-[var(--color-text-muted)] font-mono truncate" title={ctrl.controller_id}>
              {ctrl.controller_id}
            </span>
          )}
        </div>
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
          <StatusDot status={isKilled ? "stopped" : ctrl.status} />
        </div>
      </td>
      <td className="px-4 py-2.5">
        <div className="flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => toggleMutation.mutate()}
            disabled={toggleMutation.isPending}
            className={`flex items-center gap-1 rounded px-2 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${
              isKilled
                ? "text-[var(--color-green)] hover:bg-[var(--color-green)]/10"
                : "text-[var(--color-yellow)] hover:bg-[var(--color-yellow)]/10"
            }`}
            title={isKilled ? "Start controller" : "Pause controller"}
          >
            {toggleMutation.isPending ? (
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
            ) : isKilled ? (
              <Play className="h-3.5 w-3.5" />
            ) : (
              <Pause className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </td>
    </tr>
  );
}

// ── Bots Collapsible Section ──

function BotRow({ bot, server }: { bot: BotSummary; server: string }) {
  const [showLogs, setShowLogs] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const queryClient = useQueryClient();

  const stopMutation = useMutation({
    mutationFn: () => api.stopBot(server, bot.bot_name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bots", server] });
      setConfirmStop(false);
    },
  });

  const allLogs: BotLogEntry[] = useMemo(() => {
    return [
      ...(bot.error_logs || []).map((l) => ({ ...l, log_category: "error" as const })),
      ...(bot.general_logs || []).map((l) => ({ ...l, log_category: "general" as const })),
    ].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
  }, [bot.error_logs, bot.general_logs]);

  return (
    <div>
      <div
        className="flex items-center gap-4 px-4 py-2.5 text-sm cursor-pointer hover:bg-[var(--color-surface-hover)]/50 transition-colors"
        onClick={() => setShowLogs(!showLogs)}
      >
        <div className="p-0.5">
          {showLogs ? (
            <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          )}
        </div>
        <StatusDot status={bot.status} />
        <span
          className="font-medium truncate max-w-[250px]"
          title={bot.bot_name}
        >
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
        {confirmStop ? (
            <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
              <button
                onClick={() => stopMutation.mutate()}
                disabled={stopMutation.isPending}
                className="rounded px-2 py-1 text-xs font-medium bg-[var(--color-red)] text-white hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {stopMutation.isPending ? "Stopping..." : "Confirm"}
              </button>
              <button
                onClick={() => setConfirmStop(false)}
                className="rounded px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={(e) => { e.stopPropagation(); setConfirmStop(true); }}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--color-red)] hover:bg-[var(--color-red)]/10 transition-colors"
              title="Stop bot"
            >
              <Square className="h-3 w-3" />
              Stop
            </button>
          )}
      </div>
      {showLogs && (
        <div className="px-4 pb-3 pt-1">
          <LogsSection logs={allLogs} />
        </div>
      )}
      {stopMutation.isError && (
        <div className="px-4 py-2 text-xs text-[var(--color-red)]">
          Failed to stop bot: {stopMutation.error instanceof Error ? stopMutation.error.message : "Unknown error"}
        </div>
      )}
    </div>
  );
}

function BotsSection({ bots, server }: { bots: BotSummary[]; server: string }) {
  const [expanded, setExpanded] = useState(true);
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
            <BotRow key={bot.bot_name} bot={bot} server={server} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Page ──

const BOTS_WS_CHANNELS = ["bots"];

export function ActiveBotsTab() {
  const { server } = useServer();
  const [sortKey, setSortKey] = useState<SortKey>("global_pnl_quote");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [showDeploy, setShowDeploy] = useState(false);

  // Subscribe to real-time bots updates via WS
  useCondorWebSocket(BOTS_WS_CHANNELS, server);

  const { data, isLoading, error } = useQuery({
    queryKey: ["bots", server],
    queryFn: () => api.getBots(server!),
    enabled: !!server,
    refetchInterval: 30000, // Slower polling since WS handles real-time updates
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

  // Aggregate logs per bot for the overlay
  const allLogsByBot = useMemo(() => {
    const map: Record<string, BotLogEntry[]> = {};
    for (const bot of bots) {
      map[bot.bot_name] = [
        ...(bot.error_logs || []).map((l) => ({ ...l, log_category: "error" as const })),
        ...(bot.general_logs || []).map((l) => ({ ...l, log_category: "general" as const })),
      ].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    }
    return map;
  }, [bots]);

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
      {/* Summary stat cards + deploy button */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-[1fr_1fr_1fr_1fr_auto]">
        <StatCard
          label="Total PnL"
          value={formatPnl(totalPnl)}
          icon={TrendingUp}
          valueColor={pnlColor(totalPnl)}
        />
        <StatCard label="Volume" value={formatVolume(totalVolume)} icon={Volume2} />
        <StatCard label="Active Bots" value={String(activeBots)} icon={Bot} />
        <StatCard label="Controllers" value={String(controllers.length)} icon={Layers} />
        <button
          onClick={() => setShowDeploy(true)}
          className="flex items-center gap-2 justify-center rounded-lg bg-[var(--color-primary)] px-5 py-2 text-sm font-medium text-white transition-all hover:shadow-lg hover:shadow-[var(--color-primary)]/20 h-full col-span-2 lg:col-span-1"
        >
          <Rocket className="h-4 w-4" />
          Deploy Bot
        </button>
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
                      <th className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] text-center">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedControllers.map((ctrl) => (
                      <ControllerRow
                        key={`${ctrl.bot_name}-${ctrl.controller_name}`}
                        ctrl={ctrl}
                        server={server!}
                        isSelected={selectedKey === `${ctrl.bot_name}-${ctrl.controller_name}`}
                        onSelect={() => setSelectedKey(`${ctrl.bot_name}-${ctrl.controller_name}`)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Bots collapsible section */}
          {bots.length > 0 && <BotsSection bots={bots} server={server} />}
        </>
      )}

      {/* Fullscreen controller overlay */}
      {selectedKey && controllers.length > 0 && (
        <ControllerBrowser
          controllers={sortedControllers}
          server={server}
          initialControllerKey={selectedKey}
          allLogs={allLogsByBot}
          onClose={() => setSelectedKey(null)}
        />
      )}

      {/* Deploy dialog */}
      <DeployBotDialog
        open={showDeploy}
        onClose={() => setShowDeploy(false)}
        server={server}
      />
    </div>
  );
}
