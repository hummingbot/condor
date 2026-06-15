import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
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
import yamlLib from "js-yaml";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { ControllerPnlChart } from "@/components/bots/ControllerPnlChart";
import { api, type ControllerInfo } from "@/lib/api";
import { formatCurrencyVolume, formatCurrencyPnl } from "@/lib/formatters";
import { setViewContext } from "@/lib/viewContext";

type ConvertFn = (value: number, quoteCurrency: string) => { value: number; converted: boolean };

function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

function parseSide(raw: string): string {
  const dot = raw.lastIndexOf(".");
  return dot >= 0 ? raw.slice(dot + 1) : raw;
}

function StatusDot({ status }: { status: string }) {
  const isStopping = status === "stopping";
  const color =
    status === "running"
      ? "text-[var(--color-green)]"
      : status === "stopped" || status === "error"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-yellow)]";
  return isStopping ? (
    <span className="h-2.5 w-2.5 animate-spin rounded-full border-[1.5px] border-[var(--color-yellow)] border-t-transparent" />
  ) : (
    <Circle className={`h-2 w-2 fill-current ${color}`} />
  );
}

// ── Types ──

interface ControllerBrowserProps {
  controllers: ControllerInfo[];
  server: string;
  initialControllerKey: string;
  onClose: () => void;
  convert: ConvertFn;
  currencySymbol: string;
}

// Keys to strip from the YAML display (internal / read-only fields)
const YAML_HIDDEN_KEYS = new Set(["id", "controller_name", "controller_type"]);

function configToYaml(config: Record<string, unknown>): string {
  const filtered: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(config)) {
    if (!YAML_HIDDEN_KEYS.has(k) && !k.startsWith("_")) filtered[k] = v;
  }
  return yamlLib.dump(filtered, { lineWidth: -1, noRefs: true, sortKeys: true });
}

// ── YAML Config Editor ──

function YamlConfigEditor({
  config,
  server,
  configId,
  botName,
  onSaved,
}: {
  config: Record<string, unknown>;
  server: string;
  configId: string;
  botName: string;
  onSaved: () => void;
}) {
  // Memoize the dump so typing / local-state renders don't re-run yaml.dump, and
  // so WS-tick churn of the `config` object identity that yields the SAME content
  // produces an identical string (compared by value) below.
  const originalYaml = useMemo(() => configToYaml(config), [config]);
  const [yamlContent, setYamlContent] = useState(originalYaml);
  const [parseError, setParseError] = useState<string | null>(null);

  // Sync when config content actually changes (save / controller switch). Keyed
  // on the string value, not the `config` object: a tick that re-creates `config`
  // with unchanged content leaves `originalYaml` equal, so unsaved edits survive.
  useEffect(() => {
    setYamlContent(originalYaml);
    setParseError(null);
  }, [originalYaml]);

  const isDirty = yamlContent !== originalYaml;

  const handleChange = useCallback((value: string) => {
    setYamlContent(value);
    try {
      yamlLib.load(value);
      setParseError(null);
    } catch (e) {
      setParseError((e as Error).message?.split("\n")[0] || "Invalid YAML");
    }
  }, []);

  const saveMutation = useMutation({
    mutationFn: () => {
      const parsed = yamlLib.load(yamlContent) as Record<string, unknown>;
      if (!parsed || typeof parsed !== "object") {
        throw new Error("YAML must be a mapping");
      }
      return api.updateBotControllerConfig(server, botName, configId, parsed);
    },
    onSuccess: () => {
      onSaved();
    },
  });

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
              onClick={() => { setYamlContent(originalYaml); setParseError(null); }}
              className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            >
              <RotateCcw className="h-3 w-3" />
              Reset
            </button>
          )}
          <button
            onClick={() => saveMutation.mutate()}
            disabled={!isDirty || !!parseError || saveMutation.isPending}
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

      {parseError && (
        <div className="px-4 py-1.5 text-[10px] text-[var(--color-red)] bg-[var(--color-red)]/5 truncate" title={parseError}>
          {parseError}
        </div>
      )}
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

      {/* YAML editor */}
      <div className="flex-1 min-h-0">
        <CodeEditor
          value={yamlContent}
          onChange={handleChange}
          language="yaml"
          height="100%"
          className="border-0 rounded-none"
        />
      </div>
    </div>
  );
}

// ── Component ──

export function ControllerBrowser({
  controllers,
  server,
  initialControllerKey,
  onClose,
  convert,
  currencySymbol,
}: ControllerBrowserProps) {
  const cv = (val: number, pair: string) => {
    const quote = pair?.split("-")[1] || "USDT";
    return convert(val, quote).value;
  };
  const fmtPnl = (val: number, pair: string) => formatCurrencyPnl(cv(val, pair), currencySymbol);
  const fmtVol = (val: number, pair: string) => formatCurrencyVolume(cv(val, pair), currencySymbol);
  const queryClient = useQueryClient();
  const [isCompact, setIsCompact] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);

  const ctrlKey = useCallback(
    (c: ControllerInfo) => `${c.bot_name}-${c.controller_id || c.controller_name}`,
    [],
  );

  const [activeKey, setActiveKey] = useState(initialControllerKey);

  // Sync when parent changes the initial key (e.g., clicking a different controller from the table)
  useEffect(() => {
    setActiveKey(initialControllerKey);
  }, [initialControllerKey]);

  const activeCtrl = controllers.find((c) => ctrlKey(c) === activeKey) ?? controllers[0];

  const isKilled = activeCtrl?.config?.manual_kill_switch === true;
  const isStopping = activeCtrl?.status === "stopping";

  const toggleMutation = useMutation({
    mutationFn: () =>
      isKilled
        ? api.startControllers(server, activeCtrl.bot_name, [activeCtrl.controller_id])
        : api.stopControllers(server, activeCtrl.bot_name, [activeCtrl.controller_id]),
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
      // Skip if focus is inside CodeMirror editor (contenteditable div)
      const inEditor = e.target instanceof HTMLElement && e.target.closest(".cm-editor");
      if (inEditor && !e.metaKey && !e.ctrlKey) return;
      if (e.key === "ArrowUp") { goUp(); e.preventDefault(); }
      else if (e.key === "ArrowDown") { goDown(); e.preventDefault(); }
      else if (e.key === "Escape") { onClose(); e.preventDefault(); }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
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
            const ctrlStopping = c.status === "stopping";

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
                  <StatusDot status={ctrlStopping ? "stopping" : killed ? "stopped" : c.status} />
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
                  <StatusDot status={ctrlStopping ? "stopping" : killed ? "stopped" : c.status} />
                  <span className={`truncate text-xs font-medium ${isActive ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}>
                    {c.controller_id || c.controller_name}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-2 pl-4 text-[10px]">
                  {c.trading_pair && (
                    <span className="text-[var(--color-text-muted)]">{c.trading_pair}</span>
                  )}
                  <span className="ml-auto tabular-nums font-medium" style={{ color: pnlColor(c.global_pnl_quote) }}>
                    {fmtPnl(c.global_pnl_quote, c.trading_pair)}
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
              <StatusDot status={isStopping ? "stopping" : isKilled ? "stopped" : activeCtrl.status} />
              <span className="text-xs capitalize">{isStopping ? "stopping" : isKilled ? "stopped" : activeCtrl.status}</span>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            {controllers.length > 1 && (
              <div className="flex items-center border border-[var(--color-border)] rounded overflow-hidden mr-1">
                <button
                  onClick={goUp}
                  disabled={activeIdx <= 0}
                  className="p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
                  title="Previous controller (↑)"
                >
                  <ChevronUp className="h-3.5 w-3.5" />
                </button>
                <span className="text-[10px] tabular-nums text-[var(--color-text-muted)] px-1 border-x border-[var(--color-border)]">
                  {activeIdx + 1}/{controllers.length}
                </span>
                <button
                  onClick={goDown}
                  disabled={activeIdx >= controllers.length - 1}
                  className="p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
                  title="Next controller (↓)"
                >
                  <ChevronDown className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
            <button
              onClick={() => toggleMutation.mutate()}
              disabled={toggleMutation.isPending || isStopping}
              className={`flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
                isStopping
                  ? "text-[var(--color-yellow)]"
                  : isKilled
                    ? "text-[var(--color-green)] hover:bg-[var(--color-green)]/10"
                    : "text-[var(--color-yellow)] hover:bg-[var(--color-yellow)]/10"
              }`}
              title={isStopping ? "Stopping..." : isKilled ? "Start controller" : "Pause controller"}
            >
              {toggleMutation.isPending || isStopping ? (
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
            {/* PnL Evolution Chart */}
            <ControllerPnlChart
              key={configId}
              server={server}
              controllerId={configId}
              botName={activeCtrl.bot_name}
              deployedAt={activeCtrl.deployed_at}
              height={200}
              tradingPair={activeCtrl.trading_pair}
              convert={convert}
              currencySymbol={currencySymbol}
              controller={activeCtrl}
            />

            {/* PnL Breakdown - compact horizontal */}
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 text-sm">
                <div>
                  <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">Realized</div>
                  <div className="font-semibold tabular-nums" style={{ color: pnlColor(activeCtrl.realized_pnl_quote) }}>
                    {fmtPnl(activeCtrl.realized_pnl_quote, activeCtrl.trading_pair)}
                  </div>
                </div>
                <div>
                  <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">Unrealized</div>
                  <div className="font-semibold tabular-nums" style={{ color: pnlColor(activeCtrl.unrealized_pnl_quote) }}>
                    {fmtPnl(activeCtrl.unrealized_pnl_quote, activeCtrl.trading_pair)}
                  </div>
                </div>
                <div>
                  <div className="text-[var(--color-text-muted)] text-[10px] uppercase tracking-wider mb-0.5">Total PnL</div>
                  <div className="font-semibold tabular-nums" style={{ color: pnlColor(activeCtrl.global_pnl_quote) }}>
                    {fmtPnl(activeCtrl.global_pnl_quote, activeCtrl.trading_pair)}
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
                  <div className="font-semibold tabular-nums">{fmtVol(activeCtrl.volume_traded, activeCtrl.trading_pair)}</div>
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
                              {fmtPnl(realizedPnl, pair || activeCtrl.trading_pair)}
                            </div>
                          </div>
                          <div>
                            <div className="text-[var(--color-text-muted)] mb-0.5">Unrealized</div>
                            <div className="font-medium tabular-nums" style={{ color: pnlColor(unrealizedPnl) }}>
                              {fmtPnl(unrealizedPnl, pair || activeCtrl.trading_pair)}
                            </div>
                          </div>
                          <div>
                            <div className="text-[var(--color-text-muted)] mb-0.5">Volume</div>
                            <div className="font-medium tabular-nums">{fmtVol(volume, pair || activeCtrl.trading_pair)}</div>
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
            <YamlConfigEditor
              key={configId}
              config={activeCtrl.config || {}}
              server={server}
              configId={configId}
              botName={activeCtrl.bot_name}
              onSaved={() => queryClient.invalidateQueries({ queryKey: ["bots", server] })}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

