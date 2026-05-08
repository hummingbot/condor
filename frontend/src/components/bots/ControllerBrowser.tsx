import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Circle,
  Loader2,
  MessageSquare,
  Pause,
  Play,
  RotateCcw,
  Save,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { HIDDEN_KEYS, inferInputType, parseValue } from "@/components/bots/DeployBotDialog";
import { api, type BotLogEntry, type ControllerInfo } from "@/lib/api";
import { setViewContext } from "@/lib/viewContext";

// ── Shared formatters ──

function formatUsd(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return val.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function formatVolume(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(1) + "M";
  if (Math.abs(val) >= 1_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return "$" + val.toFixed(0);
}

function formatPnl(val: number) {
  return (val >= 0 ? "+" : "") + formatUsd(val);
}

function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

function parseSide(raw: string): string {
  const dot = raw.lastIndexOf(".");
  return dot >= 0 ? raw.slice(dot + 1) : raw;
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "running"
      ? "text-[var(--color-green)]"
      : status === "stopped" || status === "error"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-yellow)]";
  return <Circle className={`h-2 w-2 fill-current ${color}`} />;
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

// ── Types ──

interface ControllerBrowserProps {
  controllers: ControllerInfo[];
  server: string;
  initialControllerKey: string;
  allLogs: Record<string, BotLogEntry[]>;
  onClose: () => void;
}

// ── Inline Config Editor ──

function InlineConfigEditor({
  config,
  server,
  configId,
  onSaved,
}: {
  config: Record<string, unknown>;
  server: string;
  configId: string;
  onSaved: () => void;
}) {
  const [edits, setEdits] = useState<Record<string, string>>({});
  const entries = useMemo(
    () => Object.entries(config).filter(([k]) => !HIDDEN_KEYS.has(k)),
    [config],
  );

  const isDirty = Object.keys(edits).length > 0;

  const saveMutation = useMutation({
    mutationFn: () => {
      const parsed: Record<string, unknown> = {};
      for (const [key, raw] of Object.entries(edits)) {
        parsed[key] = parseValue(raw, inferInputType(config[key]));
      }
      return api.updateConfig(server, configId, parsed);
    },
    onSuccess: () => {
      setEdits({});
      onSaved();
    },
  });

  const handleEdit = useCallback((key: string, value: string) => {
    setEdits((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleReset = useCallback((key: string) => {
    setEdits((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Header with save */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--color-border)]/50">
        <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Config
        </h3>
        <div className="flex items-center gap-1.5">
          {isDirty && (
            <button
              onClick={() => setEdits({})}
              className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            >
              <RotateCcw className="h-3 w-3" />
              Reset
            </button>
          )}
          <button
            onClick={() => saveMutation.mutate()}
            disabled={!isDirty || saveMutation.isPending}
            className="flex items-center gap-1 rounded px-2.5 py-1 text-[10px] font-semibold transition-colors disabled:opacity-30 bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary)]/80 disabled:hover:bg-[var(--color-primary)]"
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Save className="h-3 w-3" />
            )}
            Save
          </button>
        </div>
      </div>

      {saveMutation.isError && (
        <div className="px-4 py-1.5 text-[10px] text-[var(--color-red)] bg-[var(--color-red)]/5">
          {(saveMutation.error as Error).message}
        </div>
      )}
      {saveMutation.isSuccess && !isDirty && (
        <div className="px-4 py-1.5 text-[10px] text-[var(--color-green)] bg-[var(--color-green)]/5">
          Config saved successfully
        </div>
      )}

      {/* Scrollable fields */}
      <div className="flex-1 overflow-y-auto scrollbar-thin px-4 py-3 space-y-2">
        {entries.map(([key, originalValue]) => {
          const inputType = inferInputType(originalValue);
          const isEdited = key in edits;
          const displayValue = isEdited
            ? edits[key]
            : inputType === "json"
              ? JSON.stringify(originalValue, null, 2)
              : String(originalValue ?? "");

          return (
            <div key={key} className="grid grid-cols-[minmax(100px,1fr)_1.5fr] gap-2 items-start">
              <label
                className={`text-[11px] pt-1.5 truncate ${isEdited ? "text-[var(--color-warning)] font-medium" : "text-[var(--color-text-muted)]"}`}
                title={key}
              >
                {key}
              </label>
              <div className="flex items-start gap-1">
                {inputType === "boolean" ? (
                  <button
                    onClick={() => {
                      const current = isEdited ? edits[key] === "true" : Boolean(originalValue);
                      handleEdit(key, String(!current));
                    }}
                    className={`flex items-center gap-2 rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
                      (isEdited ? edits[key] === "true" : Boolean(originalValue))
                        ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                        : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)]"
                    }`}
                  >
                    <div className={`h-3 w-3 rounded-sm border flex items-center justify-center ${
                      (isEdited ? edits[key] === "true" : Boolean(originalValue))
                        ? "border-[var(--color-primary)] bg-[var(--color-primary)]"
                        : "border-[var(--color-border)]"
                    }`}>
                      {(isEdited ? edits[key] === "true" : Boolean(originalValue)) && (
                        <Check className="h-2 w-2 text-white" />
                      )}
                    </div>
                    {isEdited ? edits[key] : String(originalValue)}
                  </button>
                ) : inputType === "json" ? (
                  <textarea
                    value={displayValue}
                    onChange={(e) => handleEdit(key, e.target.value)}
                    rows={Math.min(4, displayValue.split("\n").length + 1)}
                    className={`w-full rounded-md border bg-[var(--color-bg)] px-2 py-1 font-mono text-[11px] text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)] resize-y ${
                      isEdited ? "border-[var(--color-warning)]/60" : "border-[var(--color-border)]"
                    }`}
                  />
                ) : (
                  <input
                    type={inputType === "number" ? "number" : "text"}
                    step={inputType === "number" ? "any" : undefined}
                    value={displayValue}
                    onChange={(e) => handleEdit(key, e.target.value)}
                    className={`w-full rounded-md border bg-[var(--color-bg)] px-2 py-1 text-[11px] text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)] ${
                      inputType === "number" ? "font-mono tabular-nums" : ""
                    } ${isEdited ? "border-[var(--color-warning)]/60" : "border-[var(--color-border)]"}`}
                  />
                )}
                {isEdited && (
                  <button
                    onClick={() => handleReset(key)}
                    className="mt-0.5 p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                    title="Reset to original"
                  >
                    <RotateCcw className="h-2.5 w-2.5" />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Component ──

export function ControllerBrowser({
  controllers,
  server,
  initialControllerKey,
  allLogs,
  onClose,
}: ControllerBrowserProps) {
  const queryClient = useQueryClient();
  const [isCompact, setIsCompact] = useState(false);
  const [rightTab, setRightTab] = useState<"config" | "logs">("config");
  const sidebarRef = useRef<HTMLDivElement>(null);

  const ctrlKey = useCallback(
    (c: ControllerInfo) => `${c.bot_name}-${c.controller_name}`,
    [],
  );

  const [activeKey, setActiveKey] = useState(initialControllerKey);
  const activeCtrl = controllers.find((c) => ctrlKey(c) === activeKey) ?? controllers[0];

  const isKilled = activeCtrl?.config?.manual_kill_switch === true;

  const toggleMutation = useMutation({
    mutationFn: () =>
      isKilled
        ? api.startControllers(server, activeCtrl.bot_name, [activeCtrl.controller_name])
        : api.stopControllers(server, activeCtrl.bot_name, [activeCtrl.controller_name]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bots", server] });
    },
  });

  // Keyboard navigation
  const activeIdx = controllers.findIndex((c) => ctrlKey(c) === activeKey);

  const goUp = useCallback(() => {
    if (activeIdx > 0) setActiveKey(ctrlKey(controllers[activeIdx - 1]));
  }, [activeIdx, controllers, ctrlKey]);

  const goDown = useCallback(() => {
    if (activeIdx < controllers.length - 1) setActiveKey(ctrlKey(controllers[activeIdx + 1]));
  }, [activeIdx, controllers, ctrlKey]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowUp") { goUp(); e.preventDefault(); }
      else if (e.key === "ArrowDown") { goDown(); e.preventDefault(); }
      else if (e.key === "Escape") { onClose(); e.preventDefault(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goUp, goDown, onClose]);

  // Scroll active into view
  useEffect(() => {
    const el = sidebarRef.current?.querySelector("[data-active-ctrl]");
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeKey]);

  // View context for Agent chat integration
  useEffect(() => {
    if (activeCtrl) {
      setViewContext({
        filename: `controller:${activeCtrl.controller_id || activeCtrl.controller_name}`,
        title: `${activeCtrl.controller_name} (${activeCtrl.trading_pair})`,
        source_name: activeCtrl.bot_name,
      });
    }
    return () => setViewContext(null);
  }, [activeCtrl]);

  if (!activeCtrl) return null;

  const logs = allLogs[activeCtrl.bot_name] ?? [];
  const configId = activeCtrl.controller_id || activeCtrl.controller_name;

  return (
    <div className="fixed inset-0 z-50 flex bg-[var(--color-bg)]">
      {/* Left sidebar */}
      <div
        className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
          isCompact ? "w-12" : "w-64"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2.5">
          {!isCompact && (
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Controllers
            </span>
          )}
          <button
            onClick={() => setIsCompact(!isCompact)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            {isCompact ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
          </button>
        </div>

        {/* Controller list */}
        <div ref={sidebarRef} className="flex-1 overflow-y-auto scrollbar-thin">
          {controllers.map((c) => {
            const key = ctrlKey(c);
            const isActive = key === activeKey;
            const killed = c.config?.manual_kill_switch === true;

            if (isCompact) {
              return (
                <button
                  key={key}
                  onClick={() => setActiveKey(key)}
                  {...(isActive ? { "data-active-ctrl": true } : {})}
                  className={`flex w-full items-center justify-center py-3 transition-colors ${
                    isActive
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title={c.controller_name}
                >
                  <StatusDot status={killed ? "stopped" : c.status} />
                </button>
              );
            }

            return (
              <button
                key={key}
                onClick={() => setActiveKey(key)}
                {...(isActive ? { "data-active-ctrl": true } : {})}
                className={`w-full px-3 py-2.5 text-left transition-all ${
                  isActive
                    ? "bg-[var(--color-primary)]/5 border-l-2 border-l-[var(--color-primary)]"
                    : "border-l-2 border-l-transparent hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className="flex items-center gap-2">
                  <StatusDot status={killed ? "stopped" : c.status} />
                  <span className={`truncate text-xs font-medium ${isActive ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}>
                    {c.controller_name}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 pl-4 text-[10px]">
                  {c.trading_pair && (
                    <span className="text-[var(--color-text-muted)]">{c.trading_pair}</span>
                  )}
                  <span className="ml-auto tabular-nums font-medium" style={{ color: pnlColor(c.global_pnl_quote) }}>
                    {formatPnl(c.global_pnl_quote)}
                  </span>
                </div>
              </button>
            );
          })}
        </div>

        {/* Nav hints */}
        {!isCompact && (
          <div className="border-t border-[var(--color-border)] px-3 py-2 text-[10px] text-[var(--color-text-muted)]/60">
            <span className="flex items-center gap-1.5">
              <span className="flex items-center gap-0.5">
                <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                  <ChevronUp className="h-2.5 w-2.5" />
                </kbd>
                <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                  <ChevronDown className="h-2.5 w-2.5" />
                </kbd>
                <span className="ml-0.5">navigate</span>
              </span>
              <kbd className="inline-flex h-4 items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-1 text-[8px] font-medium">
                esc
              </kbd>
            </span>
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-2.5">
          <div className="flex items-center gap-3 min-w-0">
            <div className="truncate">
              <h2 className="text-sm font-semibold truncate">{activeCtrl.controller_name}</h2>
              {activeCtrl.controller_id && activeCtrl.controller_id !== activeCtrl.controller_name && (
                <span className="text-[10px] text-[var(--color-text-muted)] font-mono block truncate">
                  {activeCtrl.controller_id}
                </span>
              )}
            </div>
            {activeCtrl.connector && (
              <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] border border-[var(--color-border)]/50">
                {activeCtrl.connector}
              </span>
            )}
            {activeCtrl.trading_pair && (
              <span className="shrink-0 rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
                {activeCtrl.trading_pair}
              </span>
            )}
            <div className="flex items-center gap-1.5 shrink-0">
              <StatusDot status={isKilled ? "stopped" : activeCtrl.status} />
              <span className="text-xs capitalize">{isKilled ? "stopped" : activeCtrl.status}</span>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => toggleMutation.mutate()}
              disabled={toggleMutation.isPending}
              className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
                isKilled
                  ? "text-[var(--color-green)] hover:bg-[var(--color-green)]/10"
                  : "text-[var(--color-yellow)] hover:bg-[var(--color-yellow)]/10"
              }`}
              title={isKilled ? "Start controller" : "Pause controller"}
            >
              {toggleMutation.isPending ? (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : isKilled ? (
                <>
                  <Play className="h-3.5 w-3.5" />
                  Start
                </>
              ) : (
                <>
                  <Pause className="h-3.5 w-3.5" />
                  Pause
                </>
              )}
            </button>
            {/* Agent chat toggle */}
            <button
              onClick={() => {
                window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true, bubbles: true }));
              }}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium bg-amber-500/15 text-amber-500 hover:bg-amber-500/25 border border-amber-500/30 transition-all"
              title="Agent (Cmd+K)"
            >
              <MessageSquare className="h-3.5 w-3.5" />
              <span>Agent</span>
            </button>
            <button
              onClick={onClose}
              className="ml-1 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Two-column body */}
        <div className="flex flex-1 min-h-0">
          {/* Left column: Performance data */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4 min-w-0">
            {/* PnL Breakdown - compact horizontal */}
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 text-sm">
                <div>
                  <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">Realized</div>
                  <div className="font-semibold tabular-nums" style={{ color: pnlColor(activeCtrl.realized_pnl_quote) }}>
                    {formatPnl(activeCtrl.realized_pnl_quote)}
                  </div>
                </div>
                <div>
                  <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">Unrealized</div>
                  <div className="font-semibold tabular-nums" style={{ color: pnlColor(activeCtrl.unrealized_pnl_quote) }}>
                    {formatPnl(activeCtrl.unrealized_pnl_quote)}
                  </div>
                </div>
                <div>
                  <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">Total PnL</div>
                  <div className="font-semibold tabular-nums" style={{ color: pnlColor(activeCtrl.global_pnl_quote) }}>
                    {formatPnl(activeCtrl.global_pnl_quote)}
                  </div>
                </div>
                {activeCtrl.global_pnl_pct !== 0 && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">PnL %</div>
                    <div className="font-semibold tabular-nums" style={{ color: pnlColor(activeCtrl.global_pnl_pct) }}>
                      {activeCtrl.global_pnl_pct >= 0 ? "+" : ""}
                      {activeCtrl.global_pnl_pct.toFixed(2)}%
                    </div>
                  </div>
                )}
                <div>
                  <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">Volume</div>
                  <div className="font-semibold tabular-nums">{formatVolume(activeCtrl.volume_traded)}</div>
                </div>
              </div>
            </div>

            {/* Close Type Counts */}
            {Object.keys(activeCtrl.close_type_counts).length > 0 && (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-2">
                <h3 className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Close Types
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(activeCtrl.close_type_counts).map(([type, count]) => (
                    <span
                      key={type}
                      className="inline-flex items-center gap-1 rounded-md bg-[var(--color-bg)] px-2 py-0.5 text-[11px] border border-[var(--color-border)]/50"
                    >
                      <span className="text-[var(--color-text-muted)]">{type}</span>
                      <span className="font-semibold">{count}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Positions Summary */}
            {activeCtrl.positions_summary.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Positions ({activeCtrl.positions_summary.length})
                </h3>
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
                  {activeCtrl.positions_summary.map((pos, i) => {
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
                      <div key={i} className="rounded-lg border border-[var(--color-border)]/60 bg-[var(--color-bg)] p-3 space-y-2">
                        <div className="flex items-center gap-2 text-sm">
                          {connector && <span className="text-[var(--color-text-muted)] text-xs">{connector}</span>}
                          {pair && <span className="font-medium text-xs">{pair}</span>}
                          {side && (
                            <span
                              className="ml-auto rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase"
                              style={{
                                color: side.toLowerCase() === "buy" ? "var(--color-green)" : "var(--color-red)",
                                background: side.toLowerCase() === "buy" ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
                              }}
                            >
                              {side}
                            </span>
                          )}
                        </div>
                        <div className="grid grid-cols-3 gap-2 text-[11px]">
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
                        {secondaryEntries.length > 0 && (
                          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 pt-1.5 border-t border-[var(--color-border)]/30 text-[10px]">
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
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Right column: Config + Logs */}
          <div className="w-[380px] xl:w-[440px] shrink-0 border-l border-[var(--color-border)] flex flex-col bg-[var(--color-surface)]">
            {/* Tabs */}
            <div className="flex border-b border-[var(--color-border)]">
              <button
                onClick={() => setRightTab("config")}
                className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                  rightTab === "config"
                    ? "text-[var(--color-primary)] border-b-2 border-[var(--color-primary)] -mb-px"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                Config
              </button>
              <button
                onClick={() => setRightTab("logs")}
                className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                  rightTab === "logs"
                    ? "text-[var(--color-primary)] border-b-2 border-[var(--color-primary)] -mb-px"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                Logs
                {logs.filter((l) => l.log_category === "error").length > 0 && (
                  <span className="ml-1.5 inline-flex items-center justify-center rounded-full bg-[var(--color-red)]/15 px-1.5 text-[9px] font-semibold text-[var(--color-red)]">
                    {logs.filter((l) => l.log_category === "error").length}
                  </span>
                )}
              </button>
            </div>

            {/* Tab content */}
            {rightTab === "config" ? (
              <InlineConfigEditor
                key={configId}
                config={activeCtrl.config || {}}
                server={server}
                configId={configId}
                onSaved={() => queryClient.invalidateQueries({ queryKey: ["bots", server] })}
              />
            ) : (
              <div className="flex-1 overflow-y-auto">
                <LogsSection logs={logs} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Logs section ──

function LogsSection({ logs }: { logs: BotLogEntry[] }) {
  const [filter, setFilter] = useState<"all" | "error" | "general">("all");
  const filtered = filter === "all" ? logs : logs.filter((l) => l.log_category === filter);

  const errorCount = logs.filter((l) => l.log_category === "error").length;
  const generalCount = logs.filter((l) => l.log_category === "general").length;

  if (logs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-[var(--color-text-muted)]">
        <p className="text-xs">No logs available</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-[var(--color-border)]/50">
        {(["all", "general", "error"] as const).map((f) => {
          const count = f === "all" ? logs.length : f === "error" ? errorCount : generalCount;
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
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
      <div className="flex-1 overflow-y-auto font-mono text-[11px] leading-relaxed">
        {filtered.map((log, i) => (
          <div
            key={i}
            className={`flex gap-2 px-2.5 py-1 border-b border-[var(--color-border)]/10 ${
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
