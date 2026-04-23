import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  Package,
  Pencil,
  RotateCcw,
  Save,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import yaml from "js-yaml";

import { HIDDEN_KEYS } from "@/components/bots/DeployBotDialog";
import { CodeEditor } from "@/components/editor/CodeEditor";
import { useServer } from "@/hooks/useServer";
import { api, type ControllerConfigSummary } from "@/lib/api";

// ── Standalone Config Editor ──

function StandaloneConfigEditor({
  server,
  config,
  onClone,
  onDelete,
}: {
  server: string;
  config: ControllerConfigSummary;
  onClone: (config: ControllerConfigSummary) => void;
  onDelete: (configId: string) => void;
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
      <div className="flex w-full items-center bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] transition-colors">
        <button
          className="flex flex-1 items-center gap-3 px-4 py-3 text-left"
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
        {/* Action buttons */}
        <div className="flex items-center gap-1 pr-3">
          <button
            onClick={(e) => { e.stopPropagation(); onClone(config); }}
            className="p-1.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] transition-colors"
            title="Clone config"
          >
            <Copy className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(config.id); }}
            className="p-1.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-red)] hover:bg-[var(--color-red)]/10 transition-colors"
            title="Delete config"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Expanded editor */}
      {expanded && (
        <div className="border-t border-[var(--color-border)]/30">
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
              {saveMutation.isPending ? "Saving..." : saveMutation.isSuccess && !isDirty ? "Saved" : "Save"}
            </button>
          </div>

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

// ── Upload Config Dialog ──

function UploadConfigDialog({
  server,
  onClose,
}: {
  server: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [yamlContent, setYamlContent] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const handleYamlChange = useCallback((val: string) => {
    setYamlContent(val);
    try {
      const parsed = yaml.load(val);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setParseError("YAML must be a mapping");
      } else if (!(parsed as Record<string, unknown>).id) {
        setParseError("Config must have an 'id' field");
      } else {
        setParseError(null);
      }
    } catch (e) {
      setParseError(e instanceof Error ? e.message : "Invalid YAML");
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file && (file.name.endsWith(".yml") || file.name.endsWith(".yaml"))) {
      const reader = new FileReader();
      reader.onload = (ev) => {
        const text = ev.target?.result as string;
        if (text) handleYamlChange(text);
      };
      reader.readAsText(file);
    }
  }, [handleYamlChange]);

  const createMutation = useMutation({
    mutationFn: () => {
      const parsed = yaml.load(yamlContent) as Record<string, unknown>;
      const configId = String(parsed.id);
      return api.createControllerConfig(server, configId, parsed);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
      onClose();
    },
  });

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-4 md:inset-auto md:top-1/2 md:left-1/2 md:-translate-x-1/2 md:-translate-y-1/2 md:w-[560px] md:max-h-[80vh] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl z-50 flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <h2 className="text-sm font-semibold">Upload Config</h2>
          <button onClick={onClose} className="p-1 rounded hover:bg-[var(--color-surface-hover)]">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className={`rounded-lg border-2 border-dashed p-6 text-center transition-colors ${
              isDragging
                ? "border-[var(--color-primary)] bg-[var(--color-primary)]/5"
                : "border-[var(--color-border)] hover:border-[var(--color-text-muted)]"
            }`}
          >
            <Upload className="h-6 w-6 mx-auto mb-2 text-[var(--color-text-muted)]" />
            <p className="text-xs text-[var(--color-text-muted)]">
              Drop a .yml file here, or paste YAML below
            </p>
          </div>

          <CodeEditor
            value={yamlContent}
            onChange={handleYamlChange}
            language="yaml"
            height="300px"
          />

          {parseError && (
            <div className="flex items-start gap-2 rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>{parseError}</span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-[var(--color-border)]">
          {createMutation.isError && (
            <span className="text-xs text-[var(--color-red)] mr-auto">
              {createMutation.error instanceof Error ? createMutation.error.message : "Failed"}
            </span>
          )}
          <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)]">
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!!parseError || !yamlContent.trim() || createMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {createMutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            Upload
          </button>
        </div>
      </div>
    </>
  );
}

// ── Clone Config Dialog ──

function CloneConfigDialog({
  server,
  sourceConfig,
  onClose,
}: {
  server: string;
  sourceConfig: ControllerConfigSummary;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [newId, setNewId] = useState(`${sourceConfig.id}_copy`);
  const [yamlContent, setYamlContent] = useState("");
  const [yamlError, setYamlError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["config-detail", server, sourceConfig.id],
    queryFn: () => api.getConfigDetail(server, sourceConfig.id),
  });

  useMemo(() => {
    if (!data?.config) return;
    const filtered = Object.fromEntries(
      Object.entries(data.config).filter(([k]) => !HIDDEN_KEYS.has(k) && k !== "id"),
    );
    setYamlContent(yaml.dump(filtered, { sortKeys: false, lineWidth: -1 }));
  }, [data?.config]);

  const handleYamlChange = useCallback((val: string) => {
    setYamlContent(val);
    try {
      const parsed = yaml.load(val);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setYamlError("YAML must be a mapping");
      } else {
        setYamlError(null);
      }
    } catch (e) {
      setYamlError(e instanceof Error ? e.message : "Invalid YAML");
    }
  }, []);

  const createMutation = useMutation({
    mutationFn: () => {
      const parsed = yaml.load(yamlContent) as Record<string, unknown>;
      return api.createControllerConfig(server, newId, parsed);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
      onClose();
    },
  });

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-4 md:inset-auto md:top-1/2 md:left-1/2 md:-translate-x-1/2 md:-translate-y-1/2 md:w-[560px] md:max-h-[80vh] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl z-50 flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <div>
            <h2 className="text-sm font-semibold">Clone Config</h2>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              From: {sourceConfig.id}
            </p>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-[var(--color-surface-hover)]">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5 uppercase tracking-wide">
              New Config ID
            </label>
            <input
              type="text"
              value={newId}
              onChange={(e) => setNewId(e.target.value)}
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm focus:border-[var(--color-primary)] focus:outline-none"
            />
          </div>

          {isLoading ? (
            <div className="flex items-center gap-2 py-8 justify-center text-sm text-[var(--color-text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading config...
            </div>
          ) : (
            <CodeEditor
              value={yamlContent}
              onChange={handleYamlChange}
              language="yaml"
              height="350px"
            />
          )}

          {yamlError && (
            <div className="flex items-start gap-2 rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>{yamlError}</span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-[var(--color-border)]">
          {createMutation.isError && (
            <span className="text-xs text-[var(--color-red)] mr-auto">
              {createMutation.error instanceof Error ? createMutation.error.message : "Failed"}
            </span>
          )}
          <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)]">
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!newId.trim() || !!yamlError || createMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {createMutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            Clone
          </button>
        </div>
      </div>
    </>
  );
}

// ── Delete Confirmation Dialog ──

function DeleteConfirmDialog({
  server,
  configId,
  onClose,
}: {
  server: string;
  configId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteControllerConfig(server, configId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
      onClose();
    },
  });

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl z-50 p-5">
        <h2 className="text-sm font-semibold mb-2">Delete Config</h2>
        <p className="text-xs text-[var(--color-text-muted)] mb-4">
          Are you sure you want to delete <span className="font-mono font-medium text-[var(--color-text)]">{configId}</span>? This cannot be undone.
        </p>
        {deleteMutation.isError && (
          <div className="mb-3 rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
            {deleteMutation.error instanceof Error ? deleteMutation.error.message : "Delete failed"}
          </div>
        )}
        <div className="flex items-center justify-end gap-3">
          <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)]">
            Cancel
          </button>
          <button
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-red)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {deleteMutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            Delete
          </button>
        </div>
      </div>
    </>
  );
}

// ── Main Export ──

export function ConfigsTab() {
  const { server } = useServer();
  const [search, setSearch] = useState("");
  const [showUpload, setShowUpload] = useState(false);
  const [cloneTarget, setCloneTarget] = useState<ControllerConfigSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server!),
    enabled: !!server,
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
        c.trading_pair.toLowerCase().includes(q) ||
        c.controller_type.toLowerCase().includes(q),
    );
  }, [configs, search]);

  // Group by controller_type
  const grouped = useMemo(() => {
    const map = new Map<string, ControllerConfigSummary[]>();
    for (const cfg of filtered) {
      const type = cfg.controller_type || "other";
      const list = map.get(type) || [];
      list.push(cfg);
      map.set(type, list);
    }
    return map;
  }, [filtered]);

  if (!server) {
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16 text-[var(--color-text-muted)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (configs.length === 0) {
    return (
      <div className="space-y-4">
        <div className="flex justify-end">
          <button
            onClick={() => setShowUpload(true)}
            className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white"
          >
            <Upload className="h-4 w-4" />
            Upload Config
          </button>
        </div>
        <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
          <Package className="h-10 w-10" />
          <p>No saved configs</p>
        </div>
        {showUpload && <UploadConfigDialog server={server} onClose={() => setShowUpload(false)} />}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Toolbar: search + upload */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-muted)]" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter configs..."
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] pl-10 pr-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
          />
        </div>
        <button
          onClick={() => setShowUpload(true)}
          className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-all hover:shadow-lg hover:shadow-[var(--color-primary)]/20"
        >
          <Upload className="h-4 w-4" />
          Upload
        </button>
      </div>

      {/* Grouped config list */}
      {Array.from(grouped.entries()).map(([type, cfgs]) => (
        <div key={type} className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] px-1">
            {type}
            <span className="ml-2 text-[var(--color-text-muted)]/60">({cfgs.length})</span>
          </h3>
          <div className="space-y-2">
            {cfgs.map((cfg) => (
              <StandaloneConfigEditor
                key={cfg.id}
                server={server}
                config={cfg}
                onClone={setCloneTarget}
                onDelete={setDeleteTarget}
              />
            ))}
          </div>
        </div>
      ))}

      {filtered.length === 0 && search && (
        <p className="text-center text-sm text-[var(--color-text-muted)] py-8">
          No configs matching "{search}"
        </p>
      )}

      {/* Dialogs */}
      {showUpload && <UploadConfigDialog server={server} onClose={() => setShowUpload(false)} />}
      {cloneTarget && (
        <CloneConfigDialog
          server={server}
          sourceConfig={cloneTarget}
          onClose={() => setCloneTarget(null)}
        />
      )}
      {deleteTarget && (
        <DeleteConfirmDialog
          server={server}
          configId={deleteTarget}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
