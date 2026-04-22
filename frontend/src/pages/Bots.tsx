import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Circle,
  Code2,
  Layers,
  Loader2,
  Package,
  Pencil,
  Rocket,
  RotateCcw,
  Save,
  Search,
  TrendingUp,
  Volume2,
  X,
} from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import yaml from "js-yaml";

import { HIDDEN_KEYS } from "@/components/bots/DeployBotDialog";
import { DeployBotDialog } from "@/components/bots/DeployBotDialog";
import { CodeEditor } from "@/components/editor/CodeEditor";

import { useServer } from "@/hooks/useServer";
import { api, type BotSummary, type ControllerConfigSummary, type ControllerInfo } from "@/lib/api";

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

function parseSide(raw: string): string {
  // "TradeType.BUY" -> "BUY", "TradeType.SELL" -> "SELL"
  const dot = raw.lastIndexOf(".");
  return dot >= 0 ? raw.slice(dot + 1) : raw;
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

function BotLogTail({
  server,
  botName,
  open,
}: {
  server: string;
  botName: string;
  open: boolean;
}) {
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["bot-logs", server, botName],
    queryFn: () => api.getBotLogs(server, botName, 100),
    enabled: open,
    refetchInterval: open ? 5_000 : false,
    refetchIntervalInBackground: true,
  });

  if (!open) return null;

  return (
    <div className="border-t border-[var(--color-border)]/30 bg-[var(--color-bg)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-3 text-xs text-[var(--color-text-muted)]">
        <span>
          Recent log tail
          {isFetching && !isLoading ? " (updating...)" : ""}
        </span>
        <span>Auto-refresh: 5s</span>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Loading logs...
        </div>
      ) : error ? (
        <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {error instanceof Error ? error.message : "Failed to load logs"}
        </div>
      ) : (data?.tail.length ?? 0) === 0 ? (
        <div className="text-xs text-[var(--color-text-muted)]">No logs available</div>
      ) : (
        <>
          <div className="max-h-44 overflow-auto rounded-md border border-[var(--color-border)]/50 bg-[var(--color-surface)] p-2">
            <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-[var(--color-text)]">
              {data?.tail.join("\n")}
            </pre>
          </div>
          {data?.updated_at && (
            <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
              Updated {new Date(data.updated_at).toLocaleTimeString()}
            </p>
          )}
        </>
      )}
    </div>
  );
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

function PositionCard({ pos }: { pos: Record<string, unknown> }) {
  const connector = String(pos.connector_name || pos.connector || "");
  const pair = String(pos.trading_pair || "");
  const side = parseSide(String(pos.side || ""));
  const realizedPnl = Number(pos.realized_pnl_quote || 0);
  const unrealizedPnl = Number(pos.unrealized_pnl_quote || 0);
  const volume = Number(pos.volume_traded_quote || pos.volume_traded || 0);

  const primaryKeys = new Set([
    "connector_name", "connector", "trading_pair", "side",
    "realized_pnl_quote", "unrealized_pnl_quote",
    "volume_traded_quote", "volume_traded",
  ]);
  const secondaryEntries = Object.entries(pos).filter(([k]) => !primaryKeys.has(k));

  return (
    <div className="rounded-lg border border-[var(--color-border)]/60 bg-[var(--color-bg)] p-3 space-y-2">
      {/* Header: connector, pair, side */}
      <div className="flex items-center gap-2 text-sm">
        {connector && (
          <span className="text-[var(--color-text-muted)]">{connector}</span>
        )}
        {pair && <span className="font-medium">{pair}</span>}
        {side && (
          <span
            className="ml-auto rounded px-1.5 py-0.5 text-xs font-semibold uppercase"
            style={{
              color: side.toLowerCase() === "buy" ? "var(--color-green)" : "var(--color-red)",
              background: side.toLowerCase() === "buy" ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
            }}
          >
            {side}
          </span>
        )}
      </div>

      {/* PnL + Volume row */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <div className="text-[var(--color-text-muted)] mb-0.5">Realized</div>
          <div className="font-medium tabular-nums" style={{ color: pnlColor(realizedPnl) }}>
            {formatPnl(realizedPnl)}
          </div>
        </div>
        <div>
          <div className="text-[var(--color-text-muted)] mb-0.5">Unrealized</div>
          <div className="font-medium tabular-nums" style={{ color: pnlColor(unrealizedPnl) }}>
            {formatPnl(unrealizedPnl)}
          </div>
        </div>
        <div>
          <div className="text-[var(--color-text-muted)] mb-0.5">Volume</div>
          <div className="font-medium tabular-nums">{formatVolume(volume)}</div>
        </div>
      </div>

      {/* Secondary details */}
      {secondaryEntries.length > 0 && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 pt-1.5 border-t border-[var(--color-border)]/30 text-xs">
          {secondaryEntries.map(([key, val]) => (
            <div key={key} className="flex justify-between gap-1 min-w-0">
              <span className="text-[var(--color-text-muted)] truncate">{key}</span>
              <span className="tabular-nums text-right shrink-0">
                {typeof val === "number"
                  ? val.toFixed(4)
                  : String(val ?? "").includes(".")
                    ? parseSide(String(val))
                    : String(val ?? "")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DetailPanel({
  ctrl,
  onClose,
}: {
  ctrl: ControllerInfo;
  onClose: () => void;
}) {
  const [panelWidth, setPanelWidth] = useState(480);
  const isDragging = useRef(false);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;

    const onMouseMove = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      const newWidth = window.innerWidth - ev.clientX;
      setPanelWidth(Math.max(300, Math.min(newWidth, window.innerWidth * 0.8)));
    };
    const onMouseUp = () => {
      isDragging.current = false;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, []);

  const configEntries = Object.entries(ctrl.config || {});

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/30 z-40" onClick={onClose} />
      {/* Panel */}
      <div
        className="fixed top-0 right-0 h-full bg-[var(--color-bg)] border-l border-[var(--color-border)] z-50 overflow-y-auto shadow-xl"
        style={{ width: panelWidth }}
      >
        {/* Drag handle */}
        <div
          className="absolute top-0 left-0 w-1.5 h-full cursor-col-resize hover:bg-[var(--color-primary)]/30 transition-colors z-10"
          onMouseDown={onMouseDown}
        />

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
            <div className="space-y-2">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Positions ({ctrl.positions_summary.length})
              </h3>
              <div className="space-y-2">
                {ctrl.positions_summary.map((pos, i) => (
                  <PositionCard key={i} pos={pos} />
                ))}
              </div>
            </div>
          )}

          {/* Controller Config (read-only YAML) */}
          {configEntries.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Controller Config
              </h3>
              <CodeEditor
                value={yaml.dump(
                  Object.fromEntries(configEntries.filter(([k]) => !HIDDEN_KEYS.has(k))),
                  { sortKeys: false, lineWidth: -1 },
                )}
                language="yaml"
                readOnly
                height="200px"
              />
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ── Bots Collapsible Section ──

function BotsSection({
  bots,
  server,
}: {
  bots: BotSummary[];
  server: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [openLogs, setOpenLogs] = useState<Record<string, boolean>>({});
  const Chevron = expanded ? ChevronDown : ChevronRight;

  const toggleBotLogs = useCallback((botName: string) => {
    setOpenLogs((prev) => ({ ...prev, [botName]: !prev[botName] }));
  }, []);

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
            <div key={bot.bot_name}>
              <button
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-[var(--color-surface-hover)]/60 transition-colors"
                onClick={() => toggleBotLogs(bot.bot_name)}
              >
                {openLogs[bot.bot_name] ? (
                  <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                )}
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
              </button>
              <BotLogTail
                server={server}
                botName={bot.bot_name}
                open={Boolean(openLogs[bot.bot_name])}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Standalone Config Editor (for Configs tab) ──

function StandaloneConfigEditor({
  server,
  config,
}: {
  server: string;
  config: ControllerConfigSummary;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [yamlValue, setYamlValue] = useState("");
  const [originalYaml, setOriginalYaml] = useState("");
  const [yamlError, setYamlError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["config-detail", server, config.id],
    queryFn: () => api.getConfigDetail(server, config.id),
    enabled: expanded,
  });

  // Sync fetched config → YAML string
  const prevConfigRef = useRef<string>("");
  useMemo(() => {
    if (!data?.config) return;
    const filtered = Object.fromEntries(
      Object.entries(data.config).filter(([k]) => !HIDDEN_KEYS.has(k)),
    );
    const dumped = yaml.dump(filtered, { sortKeys: false, lineWidth: -1 });
    const sig = JSON.stringify(data.config);
    if (sig !== prevConfigRef.current) {
      prevConfigRef.current = sig;
      setYamlValue(dumped);
      setOriginalYaml(dumped);
      setYamlError(null);
    }
  }, [data?.config]);

  // Validate YAML on change
  const handleYamlChange = useCallback((val: string) => {
    setYamlValue(val);
    try {
      const parsed = yaml.load(val);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setYamlError("YAML must be a mapping (key: value)");
      } else {
        setYamlError(null);
      }
    } catch (e) {
      setYamlError(e instanceof Error ? e.message : "Invalid YAML");
    }
  }, []);

  const isDirty = yamlValue !== originalYaml;
  const canSave = isDirty && !yamlError;

  const saveMutation = useMutation({
    mutationFn: () => api.updateConfigYaml(server, config.id, yamlValue),
    onSuccess: () => {
      setOriginalYaml(yamlValue);
      queryClient.invalidateQueries({ queryKey: ["config-detail", server, config.id] });
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
    },
  });

  const handleSave = () => saveMutation.mutate();

  const handleReset = () => {
    setYamlValue(originalYaml);
    setYamlError(null);
  };

  return (
    <div className={`rounded-lg border overflow-hidden transition-colors ${isDirty ? "border-[var(--color-warning)]/60" : "border-[var(--color-border)]"}`}>
      {/* Header */}
      <button
        className="flex w-full items-center gap-3 px-4 py-3 text-left bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)] shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)] shrink-0" />
        )}
        <Package className="h-3.5 w-3.5 text-[var(--color-text-muted)] shrink-0" />
        <span className="text-sm font-medium truncate">{config.id}</span>
        <span className="text-xs text-[var(--color-text-muted)] truncate hidden sm:inline">
          {config.controller_name}
        </span>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {config.connector_name && (
            <span className="text-xs text-[var(--color-text-muted)]">{config.connector_name}</span>
          )}
          {config.trading_pair && (
            <span className="text-xs font-mono">{config.trading_pair}</span>
          )}
          {isDirty && (
            <span className="flex items-center gap-1 text-xs text-[var(--color-warning)]">
              <Pencil className="h-3 w-3" />
            </span>
          )}
        </div>
      </button>

      {/* Expanded editor */}
      {expanded && (
        <div className="border-t border-[var(--color-border)]/30">
          {/* Actions bar */}
          <div className="flex items-center justify-end px-4 pt-3 pb-2 gap-2">
            {isDirty && (
              <button
                onClick={handleReset}
                className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              >
                <RotateCcw className="h-3 w-3" />
                Reset
              </button>
            )}
            <button
              onClick={handleSave}
              disabled={!canSave || saveMutation.isPending}
              className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-opacity disabled:opacity-40"
            >
              {saveMutation.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : saveMutation.isSuccess && !isDirty ? (
                <Check className="h-3 w-3" />
              ) : (
                <Save className="h-3 w-3" />
              )}
              {saveMutation.isPending
                ? "Saving..."
                : saveMutation.isSuccess && !isDirty
                  ? "Saved"
                  : "Save"}
            </button>
          </div>

          {/* Error banners */}
          {yamlError && (
            <div className="mx-4 mb-2 flex items-start gap-2 rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span className="break-all">{yamlError}</span>
            </div>
          )}
          {saveMutation.isError && (
            <div className="mx-4 mb-2 rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
              {saveMutation.error instanceof Error ? saveMutation.error.message : "Save failed"}
            </div>
          )}

          {/* Editor area */}
          <div className="px-4 pb-4">
            {isLoading ? (
              <div className="flex items-center gap-2 py-6 text-sm text-[var(--color-text-muted)]">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
                Loading config...
              </div>
            ) : (
              <CodeEditor
                value={yamlValue}
                onChange={handleYamlChange}
                language="yaml"
                height="400px"
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Configs Tab ──

function ConfigsTab({ server }: { server: string }) {
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server),
  });

  const configs = data?.configs ?? [];

  const filtered = useMemo(() => {
    if (!search.trim()) return configs;
    const q = search.toLowerCase();
    return configs.filter(
      (c) =>
        c.id.toLowerCase().includes(q) ||
        c.controller_name.toLowerCase().includes(q) ||
        c.connector_name.toLowerCase().includes(q) ||
        c.trading_pair.toLowerCase().includes(q),
    );
  }, [configs, search]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16 text-[var(--color-text-muted)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (configs.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
        <Package className="h-10 w-10" />
        <p>No saved configs</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-muted)]" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter configs..."
          className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] pl-10 pr-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
        />
      </div>

      {/* Config list */}
      <div className="space-y-2">
        {filtered.map((cfg) => (
          <StandaloneConfigEditor key={cfg.id} server={server} config={cfg} />
        ))}
      </div>

      {filtered.length === 0 && search && (
        <p className="text-center text-sm text-[var(--color-text-muted)] py-8">
          No configs matching "{search}"
        </p>
      )}
    </div>
  );
}

// ── Controller Source Viewer ──

function ControllerSourceViewer({
  server,
  controllerType,
  controllerName,
}: {
  server: string;
  controllerType: string;
  controllerName: string;
}) {
  const [expanded, setExpanded] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["controller-source", server, controllerType, controllerName],
    queryFn: () => api.getControllerSource(server, controllerType, controllerName),
    enabled: expanded,
  });

  return (
    <div className="rounded-lg border border-[var(--color-border)] overflow-hidden">
      <button
        className="flex w-full items-center gap-3 px-4 py-2.5 text-left bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)] shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)] shrink-0" />
        )}
        <Code2 className="h-3.5 w-3.5 text-[var(--color-text-muted)] shrink-0" />
        <span className="text-sm font-medium truncate">{controllerName}</span>
      </button>

      {expanded && (
        <div className="border-t border-[var(--color-border)]/30 px-4 py-3">
          {isLoading ? (
            <div className="flex items-center gap-2 py-6 text-sm text-[var(--color-text-muted)]">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
              Loading source...
            </div>
          ) : isError ? (
            <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
              {error instanceof Error ? error.message : "Failed to load source"}
            </div>
          ) : (
            <CodeEditor
              value={data?.source ?? ""}
              language="python"
              readOnly
              height="500px"
            />
          )}
        </div>
      )}
    </div>
  );
}

// ── Controllers Tab ──

function ControllersTab({ server }: { server: string }) {
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server),
  });

  const controllerTypes = data?.controller_types ?? {};

  // Flatten for search and organize by type
  const allControllers = useMemo(() => {
    const result: { type: string; name: string }[] = [];
    for (const [type, names] of Object.entries(controllerTypes)) {
      for (const name of names) {
        result.push({ type, name });
      }
    }
    return result;
  }, [controllerTypes]);

  const filtered = useMemo(() => {
    if (!search.trim()) return allControllers;
    const q = search.toLowerCase();
    return allControllers.filter(
      (c) => c.name.toLowerCase().includes(q) || c.type.toLowerCase().includes(q),
    );
  }, [allControllers, search]);

  // Group filtered results by type
  const grouped = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const c of filtered) {
      const list = map.get(c.type) || [];
      list.push(c.name);
      map.set(c.type, list);
    }
    return map;
  }, [filtered]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16 text-[var(--color-text-muted)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (allControllers.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
        <Code2 className="h-10 w-10" />
        <p>No controllers found</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-muted)]" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter controllers..."
          className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] pl-10 pr-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
        />
      </div>

      {/* Grouped by type */}
      {Array.from(grouped.entries()).map(([type, names]) => (
        <div key={type} className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] px-1">
            {type}
            <span className="ml-2 text-[var(--color-text-muted)]/60">({names.length})</span>
          </h3>
          <div className="space-y-2">
            {names.map((name) => (
              <ControllerSourceViewer
                key={`${type}-${name}`}
                server={server}
                controllerType={type}
                controllerName={name}
              />
            ))}
          </div>
        </div>
      ))}

      {filtered.length === 0 && search && (
        <p className="text-center text-sm text-[var(--color-text-muted)] py-8">
          No controllers matching "{search}"
        </p>
      )}
    </div>
  );
}

// ── Main Page ──

type Tab = "running" | "configs" | "controllers";

export function Bots() {
  const { server } = useServer();
  const [tab, setTab] = useState<Tab>("running");
  const [sortKey, setSortKey] = useState<SortKey>("global_pnl_quote");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedController, setSelectedController] = useState<ControllerInfo | null>(null);
  const [showDeploy, setShowDeploy] = useState(false);

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
      {/* Header: tabs + deploy button */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
          <button
            onClick={() => setTab("running")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === "running"
                ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            Running
          </button>
          <button
            onClick={() => setTab("configs")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === "configs"
                ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            Configs
          </button>
          <button
            onClick={() => setTab("controllers")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === "controllers"
                ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            Controllers
          </button>
        </div>
        <button
          onClick={() => setShowDeploy(true)}
          className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-all hover:shadow-lg hover:shadow-[var(--color-primary)]/20"
        >
          <Rocket className="h-4 w-4" />
          Deploy Bot
        </button>
      </div>

      {tab === "configs" ? (
        <ConfigsTab server={server} />
      ) : tab === "controllers" ? (
        <ControllersTab server={server} />
      ) : (
        <>
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
              {bots.length > 0 && <BotsSection bots={bots} server={server} />}
            </>
          )}

          {/* Side panel */}
          {selectedController && (
            <DetailPanel ctrl={selectedController} onClose={() => setSelectedController(null)} />
          )}
        </>
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
