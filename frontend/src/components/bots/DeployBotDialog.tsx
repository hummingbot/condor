import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Package,
  Pencil,
  Rocket,
  RotateCcw,
  Search,
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
}: {
  server: string;
  configId: string;
  onDirtyChange: (configId: string, edits: Record<string, unknown> | null) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["config-detail", server, configId],
    queryFn: () => api.getConfigDetail(server, configId),
  });

  const [edits, setEdits] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState(true);

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
      <button
        className="flex w-full items-center gap-2 px-4 py-3 text-left bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        )}
        <Package className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <span className="text-sm font-medium truncate flex-1">{configId}</span>
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

// ── Config selection inline detail (read-only, for step 1) ──

function ConfigDetailInline({
  server,
  configId,
}: {
  server: string;
  configId: string;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["config-detail", server, configId],
    queryFn: () => api.getConfigDetail(server, configId),
  });

  if (isLoading) {
    return (
      <div className="px-4 py-2 text-xs text-[var(--color-text-muted)]">
        Loading...
      </div>
    );
  }

  if (!data) return null;

  const entries = Object.entries(data.config).filter(
    ([k]) => !HIDDEN_KEYS.has(k),
  );

  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1 px-4 py-2 bg-[var(--color-bg)] border-t border-[var(--color-border)]/30 text-xs">
      {entries.map(([key, val]) => (
        <div key={key} className="flex justify-between gap-2 py-0.5 min-w-0">
          <span className="text-[var(--color-text-muted)] truncate">{key}</span>
          <span className="tabular-nums text-right truncate" title={String(val ?? "")}>
            {typeof val === "object" && val !== null
              ? JSON.stringify(val)
              : String(val ?? "")}
          </span>
        </div>
      ))}
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
  const [step, setStep] = useState<1 | 2>(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expandedConfig, setExpandedConfig] = useState<string | null>(null);
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");

  // Step 2 fields
  const [botName, setBotName] = useState(
    () => `bot_${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "").slice(0, 15)}`,
  );
  const [accountName, setAccountName] = useState("master_account");
  const [image, setImage] = useState("hummingbot/hummingbot:latest");
  const [maxGlobalDrawdown, setMaxGlobalDrawdown] = useState("");
  const [maxControllerDrawdown, setMaxControllerDrawdown] = useState("");
  const [deployError, setDeployError] = useState<string | null>(null);

  // Track edits per config: configId -> edited fields (null = not dirty)
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
      if (edits === null) {
        delete next[configId];
      } else {
        next[configId] = edits;
      }
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

  const groupedConfigs = useMemo(() => {
    const groups: Record<string, ControllerConfigSummary[]> = {};
    for (const c of filteredConfigs) {
      const type = c.controller_type || "other";
      if (!groups[type]) groups[type] = [];
      groups[type].push(c);
    }
    return groups;
  }, [filteredConfigs]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleType = (type: string) => {
    setExpandedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  // Save modified configs then deploy
  const deployMutation = useMutation({
    mutationFn: async () => {
      // First, save any configs that were edited
      for (const [configId, edits] of dirtyConfigs) {
        await api.updateConfig(server, configId, edits);
      }

      // Then deploy
      return api.deployBot(server, {
        bot_name: botName,
        controllers_config: Array.from(selected),
        account_name: accountName,
        image,
        max_global_drawdown_quote: maxGlobalDrawdown ? parseFloat(maxGlobalDrawdown) : null,
        max_controller_drawdown_quote: maxControllerDrawdown
          ? parseFloat(maxControllerDrawdown)
          : null,
      });
    },
    onSuccess: () => {
      // Invalidate config caches since we may have updated them
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
    setStep(1);
    setSelected(new Set());
    setExpandedConfig(null);
    setExpandedTypes(new Set());
    setSearch("");
    setDeployError(null);
    setConfigEdits({});
    onClose();
  };

  if (!open) return null;

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
            {step === 2 && (
              <button
                onClick={() => { setStep(1); setDeployError(null); }}
                className="p-1 rounded hover:bg-[var(--color-surface-hover)] transition-colors"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
            )}
            <Rocket className="h-5 w-5 text-[var(--color-primary)]" />
            <h2 className="text-lg font-semibold text-[var(--color-text)]">
              {step === 1 ? "Select Configs" : "Review & Deploy"}
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
        <div className="flex-1 overflow-y-auto p-6">
          {step === 1 ? (
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
                  autoFocus
                />
              </div>

              {isLoading ? (
                <div className="flex h-40 items-center justify-center text-[var(--color-text-muted)]">
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
                </div>
              ) : filteredConfigs.length === 0 ? (
                <div className="flex h-40 flex-col items-center justify-center text-[var(--color-text-muted)]">
                  <Package className="mb-2 h-8 w-8 opacity-30" />
                  <p className="text-sm">No configs found</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {Object.entries(groupedConfigs).map(([type, cfgs]) => {
                    const isTypeExpanded = expandedTypes.has(type);
                    return (
                      <div
                        key={type}
                        className="rounded-lg border border-[var(--color-border)] overflow-hidden"
                      >
                        <button
                          className="flex w-full items-center gap-2 px-4 py-2.5 text-left bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] transition-colors"
                          onClick={() => toggleType(type)}
                        >
                          {isTypeExpanded ? (
                            <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                          )}
                          <span className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                            {type}
                          </span>
                          <span className="text-xs text-[var(--color-text-muted)]">
                            ({cfgs.length})
                          </span>
                        </button>

                        {isTypeExpanded && (
                          <div className="divide-y divide-[var(--color-border)]/30">
                            {cfgs.map((cfg) => {
                              const isSelected = selected.has(cfg.id);
                              const isExpanded = expandedConfig === cfg.id;
                              return (
                                <div key={cfg.id}>
                                  <div
                                    className={`flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors hover:bg-[var(--color-surface-hover)]/50 ${
                                      isSelected ? "bg-[var(--color-primary)]/5" : ""
                                    }`}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={isSelected}
                                      onChange={() => toggleSelect(cfg.id)}
                                      className="h-4 w-4 rounded border-[var(--color-border)] accent-[var(--color-primary)]"
                                    />
                                    <button
                                      className="flex-1 flex items-center gap-3 text-left min-w-0"
                                      onClick={() =>
                                        setExpandedConfig(isExpanded ? null : cfg.id)
                                      }
                                    >
                                      <span className="text-sm font-medium truncate">
                                        {cfg.id}
                                      </span>
                                      <span className="text-xs text-[var(--color-text-muted)] truncate">
                                        {cfg.controller_name}
                                      </span>
                                      {cfg.connector_name && (
                                        <span className="text-xs text-[var(--color-text-muted)]">
                                          {cfg.connector_name}
                                        </span>
                                      )}
                                      {cfg.trading_pair && (
                                        <span className="text-xs font-mono">
                                          {cfg.trading_pair}
                                        </span>
                                      )}
                                      <span className="ml-auto">
                                        {isExpanded ? (
                                          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                                        ) : (
                                          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                                        )}
                                      </span>
                                    </button>
                                  </div>
                                  {isExpanded && (
                                    <ConfigDetailInline
                                      server={server}
                                      configId={cfg.id}
                                    />
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
            /* Step 2: Review configs + Deploy settings */
            <div className="space-y-6">
              {/* Config editors */}
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                  Controller Configs ({selected.size})
                </h3>
                <div className="space-y-3">
                  {Array.from(selected).map((id) => (
                    <ConfigEditor
                      key={id}
                      server={server}
                      configId={id}
                      onDirtyChange={handleDirtyChange}
                    />
                  ))}
                </div>
              </div>

              {/* Divider */}
              <div className="border-t border-[var(--color-border)]" />

              {/* Deploy settings */}
              <div>
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                  Bot Settings
                </h3>
                <div className="space-y-3">
                  <div>
                    <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
                      Bot Name
                    </label>
                    <input
                      type="text"
                      value={botName}
                      onChange={(e) => setBotName(e.target.value)}
                      className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
                        Account
                      </label>
                      <input
                        type="text"
                        value={accountName}
                        onChange={(e) => setAccountName(e.target.value)}
                        className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
                        Image
                      </label>
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
                      <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
                        Max Global Drawdown
                      </label>
                      <input
                        type="number"
                        value={maxGlobalDrawdown}
                        onChange={(e) => setMaxGlobalDrawdown(e.target.value)}
                        placeholder="Optional"
                        className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
                        Max Controller Drawdown
                      </label>
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
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-[var(--color-border)] px-6 py-4">
          <span className="text-xs text-[var(--color-text-muted)]">
            {step === 1
              ? `${selected.size} config${selected.size !== 1 ? "s" : ""} selected`
              : dirtyConfigs.length > 0
                ? `${dirtyConfigs.length} config${dirtyConfigs.length !== 1 ? "s" : ""} modified`
                : ""}
          </span>
          <div className="flex gap-3">
            <button
              onClick={handleClose}
              className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            >
              Cancel
            </button>
            {step === 1 ? (
              <button
                onClick={() => setStep(2)}
                disabled={selected.size === 0}
                className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-40"
              >
                Next
              </button>
            ) : (
              <button
                onClick={() => deployMutation.mutate()}
                disabled={!botName.trim() || deployMutation.isPending}
                className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-40"
              >
                <Rocket className="h-4 w-4" />
                {deployMutation.isPending
                  ? dirtyConfigs.length > 0
                    ? "Saving & Deploying..."
                    : "Deploying..."
                  : dirtyConfigs.length > 0
                    ? "Save & Deploy"
                    : "Deploy"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
