import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Package,
  Pencil,
  Rocket,
  RotateCcw,
  Search,
  Settings,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  api,
  type ControllerConfigSummary,
} from "@/lib/api";

// ── Helpers ──

export const HIDDEN_KEYS = new Set([
  "id",
  "controller_name",
  "controller_type",
  "candles_config",
]);

export function inferInputType(value: unknown): "number" | "boolean" | "text" | "json" {
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  if (typeof value === "object" && value !== null) return "json";
  return "text";
}

export function parseValue(raw: string, type: "number" | "boolean" | "text" | "json"): unknown {
  if (type === "number") {
    const n = Number(raw);
    return isNaN(n) ? raw : n;
  }
  if (type === "boolean") return raw === "true";
  if (type === "json") {
    try { return JSON.parse(raw); } catch { return raw; }
  }
  return raw;
}

// ── Config Editor for a single config ──

export function ConfigEditor({
  server,
  configId,
  onDirtyChange,
  onRemove,
}: {
  server: string;
  configId: string;
  onDirtyChange: (configId: string, edits: Record<string, unknown> | null) => void;
  onRemove?: (configId: string) => void;
}) {
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["config-detail", server, configId],
    queryFn: () => api.getConfigDetail(server, configId),
    enabled: expanded,
  });

  const config = data?.config ?? {};
  const entries = useMemo(
    () => Object.entries(config).filter(([k]) => !HIDDEN_KEYS.has(k)),
    [config],
  );

  const isDirty = Object.keys(edits).length > 0;

  // Notify parent about dirty state
  useEffect(() => {
    if (!isDirty) {
      onDirtyChange(configId, null);
      return;
    }
    const parsed: Record<string, unknown> = {};
    for (const [key, raw] of Object.entries(edits)) {
      const originalValue = config[key];
      parsed[key] = parseValue(raw, inferInputType(originalValue));
    }
    onDirtyChange(configId, parsed);
  }, [edits, configId]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const handleResetAll = useCallback(() => {
    setEdits({});
  }, []);

  if (isLoading) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] p-4">
        <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
          Loading {configId}...
        </div>
      </div>
    );
  }

  return (
    <div className={`rounded-lg border overflow-hidden transition-colors ${isDirty ? "border-[var(--color-warning)]/60" : "border-[var(--color-border)]"}`}>
      {/* Header */}
      <div className="flex items-center bg-[var(--color-surface)]">
        <button
          className="flex flex-1 items-center gap-2 px-4 py-3 text-left hover:bg-[var(--color-surface-hover)] transition-colors"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          )}
          <Package className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          <span className="text-sm font-medium truncate">{configId}</span>
          {data?.controller_name && (
            <span className="text-xs text-[var(--color-text-muted)]">
              {data.controller_name}
            </span>
          )}
          {isDirty && (
            <span className="flex items-center gap-1 text-xs text-[var(--color-warning)]">
              <Pencil className="h-3 w-3" />
              {Object.keys(edits).length} edited
            </span>
          )}
        </button>
        {onRemove && (
          <button
            onClick={() => onRemove(configId)}
            className="px-3 py-3 text-[var(--color-text-muted)] hover:text-[var(--color-red)] transition-colors"
            title="Remove config"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Fields */}
      {expanded && (
        <div className="border-t border-[var(--color-border)]/30">
          {/* Reset all button */}
          {isDirty && (
            <div className="flex justify-end px-4 pt-2">
              <button
                onClick={(e) => { e.stopPropagation(); handleResetAll(); }}
                className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              >
                <RotateCcw className="h-3 w-3" />
                Reset all
              </button>
            </div>
          )}
          <div className="grid gap-2 p-4 pt-2">
            {entries.map(([key, originalValue]) => {
              const inputType = inferInputType(originalValue);
              const isEdited = key in edits;
              const displayValue = isEdited
                ? edits[key]
                : inputType === "json"
                  ? JSON.stringify(originalValue, null, 2)
                  : String(originalValue ?? "");

              return (
                <div key={key} className="grid grid-cols-[minmax(120px,1fr)_2fr] gap-2 items-start">
                  <label
                    className={`text-xs pt-2 truncate ${isEdited ? "text-[var(--color-warning)] font-medium" : "text-[var(--color-text-muted)]"}`}
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
                        className={`flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs transition-colors ${
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
                        {(isEdited ? edits[key] : String(originalValue))}
                      </button>
                    ) : inputType === "json" ? (
                      <textarea
                        value={displayValue}
                        onChange={(e) => handleEdit(key, e.target.value)}
                        rows={Math.min(6, displayValue.split("\n").length + 1)}
                        className={`w-full rounded-md border bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-xs text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)] resize-y ${
                          isEdited ? "border-[var(--color-warning)]/60" : "border-[var(--color-border)]"
                        }`}
                      />
                    ) : (
                      <input
                        type={inputType === "number" ? "number" : "text"}
                        step={inputType === "number" ? "any" : undefined}
                        value={displayValue}
                        onChange={(e) => handleEdit(key, e.target.value)}
                        className={`w-full rounded-md border bg-[var(--color-bg)] px-2.5 py-1.5 text-xs text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)] ${
                          inputType === "number" ? "font-mono tabular-nums" : ""
                        } ${isEdited ? "border-[var(--color-warning)]/60" : "border-[var(--color-border)]"}`}
                      />
                    )}
                    {isEdited && (
                      <button
                        onClick={() => handleReset(key)}
                        className="mt-1 p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                        title="Reset to original"
                      >
                        <RotateCcw className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Dialog ──

export function DeployBotDialog({
  open,
  onClose,
  server,
}: {
  open: boolean;
  onClose: () => void;
  server: string;
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  // Bot settings
  const [botName, setBotName] = useState(
    () => `bot_${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "").slice(0, 15)}`,
  );
  const [accountName, setAccountName] = useState("master_account");
  const [image, setImage] = useState("hummingbot/hummingbot:latest");
  const [maxGlobalDrawdown, setMaxGlobalDrawdown] = useState("");
  const [maxControllerDrawdown, setMaxControllerDrawdown] = useState("");

  // Track edits per config
  const [configEdits, setConfigEdits] = useState<Record<string, Record<string, unknown> | null>>({});

  const dirtyConfigs = useMemo(
    () => Object.entries(configEdits).filter(([, v]) => v !== null) as [string, Record<string, unknown>][],
    [configEdits],
  );

  const handleDirtyChange = useCallback((configId: string, edits: Record<string, unknown> | null) => {
    setConfigEdits((prev) => {
      if (prev[configId] === edits) return prev;
      if (edits === null && !(configId in prev)) return prev;
      const next = { ...prev };
      if (edits === null) delete next[configId];
      else next[configId] = edits;
      return next;
    });
  }, []);

  const { data, isLoading } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server),
    enabled: open,
  });

  const configs = data?.configs ?? [];

  const filteredConfigs = useMemo(() => {
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

  // Group unselected configs by type for browsing
  const unselectedConfigs = useMemo(
    () => filteredConfigs.filter((c) => !selected.has(c.id)),
    [filteredConfigs, selected],
  );

  const groupedUnselected = useMemo(() => {
    const groups: Record<string, ControllerConfigSummary[]> = {};
    for (const c of unselectedConfigs) {
      const type = c.controller_type || "other";
      (groups[type] ??= []).push(c);
    }
    return groups;
  }, [unselectedConfigs]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        setConfigEdits((ce) => { const n = { ...ce }; delete n[id]; return n; });
      } else {
        next.add(id);
      }
      return next;
    });
  };

  // Deploy
  const deployMutation = useMutation({
    mutationFn: async () => {
      for (const [configId, edits] of dirtyConfigs) {
        await api.updateConfig(server, configId, edits);
      }
      return api.deployBot(server, {
        bot_name: botName,
        controllers_config: Array.from(selected),
        account_name: accountName,
        image,
        max_global_drawdown_quote: maxGlobalDrawdown ? parseFloat(maxGlobalDrawdown) : null,
        max_controller_drawdown_quote: maxControllerDrawdown ? parseFloat(maxControllerDrawdown) : null,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bots", server] });
      queryClient.invalidateQueries({ queryKey: ["config-detail"] });
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
      handleClose();
    },
    onError: (err) => {
      setDeployError(err instanceof Error ? err.message : "Deployment failed");
    },
  });

  const handleClose = () => {
    setSelected(new Set());
    setSearch("");
    setShowAdvanced(false);
    setDeployError(null);
    setConfigEdits({});
    onClose();
  };

  // Reset bot name on open
  useEffect(() => {
    if (open) {
      setBotName(`bot_${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "").slice(0, 15)}`);
    }
  }, [open]);

  if (!open) return null;

  const hasSelected = selected.size > 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={handleClose}
    >
      <div
        className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-4">
          <div className="flex items-center gap-3">
            <Rocket className="h-5 w-5 text-[var(--color-primary)]" />
            <h2 className="text-lg font-semibold text-[var(--color-text)]">
              Deploy Bot
            </h2>
          </div>
          <button
            onClick={handleClose}
            className="p-1 rounded hover:bg-[var(--color-surface-hover)] transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">

          {/* Selected configs with inline editors */}
          {hasSelected && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                Selected ({selected.size})
              </h3>
              <div className="space-y-2">
                {Array.from(selected).map((id) => (
                  <ConfigEditor
                    key={id}
                    server={server}
                    configId={id}
                    onDirtyChange={handleDirtyChange}
                    onRemove={toggleSelect}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Available configs */}
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
              {hasSelected ? "Add more" : "Select configs"}
            </h3>
            <div className="relative mb-3">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-muted)]" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search configs..."
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] pl-10 pr-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
                autoFocus
              />
            </div>

            {isLoading ? (
              <div className="flex h-24 items-center justify-center text-[var(--color-text-muted)]">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
              </div>
            ) : unselectedConfigs.length === 0 ? (
              <div className="flex h-16 items-center justify-center text-[var(--color-text-muted)]">
                <p className="text-xs">{configs.length === 0 ? "No configs available" : "All configs selected"}</p>
              </div>
            ) : (
              <div className="rounded-lg border border-[var(--color-border)] overflow-hidden max-h-[300px] overflow-y-auto">
                {Object.entries(groupedUnselected).map(([type, cfgs]) => (
                  <div key={type}>
                    <div className="px-4 py-1.5 bg-[var(--color-surface)] text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)] sticky top-0 border-b border-[var(--color-border)]/30">
                      {type} ({cfgs.length})
                    </div>
                    {cfgs.map((cfg) => (
                      <button
                        key={cfg.id}
                        onClick={() => toggleSelect(cfg.id)}
                        className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-[var(--color-surface-hover)]/50 transition-colors border-b border-[var(--color-border)]/20 last:border-b-0"
                      >
                        <div className="h-4 w-4 rounded border border-[var(--color-border)] flex items-center justify-center shrink-0" />
                        <span className="text-sm font-medium truncate">{cfg.id}</span>
                        {cfg.connector_name && (
                          <span className="text-xs text-[var(--color-text-muted)]">{cfg.connector_name}</span>
                        )}
                        {cfg.trading_pair && (
                          <span className="text-xs font-mono text-[var(--color-text-muted)]">{cfg.trading_pair}</span>
                        )}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Advanced settings (collapsed by default) */}
          <div className="border-t border-[var(--color-border)] pt-4">
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            >
              <Settings className="h-3.5 w-3.5" />
              <span className="font-medium">Advanced Settings</span>
              {showAdvanced ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
            </button>

            {showAdvanced && (
              <div className="mt-3 space-y-3">
                <div>
                  <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Bot Name</label>
                  <input
                    type="text"
                    value={botName}
                    onChange={(e) => setBotName(e.target.value)}
                    className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]"
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Account</label>
                    <input
                      type="text"
                      value={accountName}
                      onChange={(e) => setAccountName(e.target.value)}
                      className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Image</label>
                    <input
                      type="text"
                      value={image}
                      onChange={(e) => setImage(e.target.value)}
                      className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Max Global Drawdown</label>
                    <input
                      type="number"
                      value={maxGlobalDrawdown}
                      onChange={(e) => setMaxGlobalDrawdown(e.target.value)}
                      placeholder="Optional"
                      className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-[var(--color-text-muted)]">Max Controller Drawdown</label>
                    <input
                      type="number"
                      value={maxControllerDrawdown}
                      onChange={(e) => setMaxControllerDrawdown(e.target.value)}
                      placeholder="Optional"
                      className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          {deployError && (
            <div className="rounded-lg border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-4 py-3">
              <p className="text-sm text-[var(--color-red)]">{deployError}</p>
              <button
                onClick={() => { setDeployError(null); deployMutation.mutate(); }}
                className="mt-2 text-xs font-medium text-[var(--color-red)] underline"
              >
                Retry
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-[var(--color-border)] px-6 py-4">
          <span className="text-xs text-[var(--color-text-muted)]">
            {selected.size} config{selected.size !== 1 ? "s" : ""}
            {dirtyConfigs.length > 0 && ` · ${dirtyConfigs.length} modified`}
          </span>
          <div className="flex gap-3">
            <button
              onClick={handleClose}
              className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            >
              Cancel
            </button>
            <button
              onClick={() => deployMutation.mutate()}
              disabled={!hasSelected || !botName.trim() || deployMutation.isPending}
              className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-40"
            >
              <Rocket className="h-4 w-4" />
              {deployMutation.isPending
                ? dirtyConfigs.length > 0 ? "Saving & Deploying..." : "Deploying..."
                : dirtyConfigs.length > 0 ? "Save & Deploy" : "Deploy"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
