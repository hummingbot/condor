import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Code2,
  Loader2,
  Plus,
  Search,
  X,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

// ── Controller Source Viewer ──

function ControllerSourceViewer({
  server,
  controllerType,
  controllerName,
  onNewConfig,
}: {
  server: string;
  controllerType: string;
  controllerName: string;
  onNewConfig: (type: string, name: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["controller-source", server, controllerType, controllerName],
    queryFn: () => api.getControllerSource(server, controllerType, controllerName),
    enabled: expanded,
  });

  return (
    <div className="rounded-lg border border-[var(--color-border)] overflow-hidden">
      <div className="flex w-full items-center bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)] transition-colors">
        <button
          className="flex flex-1 items-center gap-3 px-4 py-2.5 text-left"
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
        <button
          onClick={() => onNewConfig(controllerType, controllerName)}
          className="flex items-center gap-1 mr-3 px-2 py-1 rounded text-xs text-[var(--color-primary)] hover:bg-[var(--color-primary)]/10 transition-colors"
          title="Create new config from this controller"
        >
          <Plus className="h-3 w-3" />
          New Config
        </button>
      </div>

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

// ── New Config Dialog ──

function NewConfigDialog({
  server,
  controllerType,
  controllerName,
  onClose,
}: {
  server: string;
  controllerType: string;
  controllerName: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [configId, setConfigId] = useState("");
  const [fieldValues, setFieldValues] = useState<Record<string, unknown>>({});

  const { data: template, isLoading } = useQuery({
    queryKey: ["controller-template", server, controllerType, controllerName],
    queryFn: () => api.getControllerConfigTemplate(server, controllerType, controllerName),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> = {
        ...hiddenDefaults,
        ...fieldValues,
        controller_name: controllerName,
        controller_type: controllerType,
      };
      return api.createControllerConfig(server, configId, config);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
      onClose();
    },
  });

  // Fields to always hide (initialized with defaults silently)
  const HIDDEN_FIELDS = new Set(["id", "controller_name", "controller_type", "manual_kill_switch", "initial_positions"]);

  // Parse template fields
  const { visibleFields, hiddenDefaults } = useMemo(() => {
    if (!template) return { visibleFields: [], hiddenDefaults: {} as Record<string, unknown> };
    const raw = template.fields ?? template;
    let allFields: { name: string; type: string; default: unknown; description?: string }[];
    if (Array.isArray(raw)) {
      allFields = raw as typeof allFields;
    } else {
      allFields = Object.entries(raw)
        .map(([name, info]) => {
          const field = info as Record<string, unknown>;
          return {
            name,
            type: String(field.type ?? "string"),
            default: field.default,
            description: String(field.description ?? ""),
          };
        });
    }
    const visible: typeof allFields = [];
    const defaults: Record<string, unknown> = {};
    for (const f of allFields) {
      if (HIDDEN_FIELDS.has(f.name)) {
        if (f.default !== undefined && f.default !== null) {
          defaults[f.name] = f.default;
        }
      } else {
        visible.push(f);
      }
    }
    return { visibleFields: visible, hiddenDefaults: defaults };
  }, [template]);

  // Init defaults when template loads
  useMemo(() => {
    if (visibleFields.length === 0) return;
    const defaults: Record<string, unknown> = {};
    for (const f of visibleFields) {
      if (f.default !== undefined && f.default !== null) {
        defaults[f.name] = f.default;
      }
    }
    setFieldValues((prev) => ({ ...defaults, ...prev }));
  }, [visibleFields]);

  const updateField = useCallback((name: string, value: unknown) => {
    setFieldValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-4 md:inset-auto md:top-1/2 md:left-1/2 md:-translate-x-1/2 md:-translate-y-1/2 md:w-[680px] md:max-h-[80vh] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl z-50 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <div>
            <h2 className="text-sm font-semibold">New Config</h2>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              {controllerType} / {controllerName}
            </p>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-[var(--color-surface-hover)]">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Config ID — full width */}
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1.5 uppercase tracking-wide">
              Config ID *
            </label>
            <input
              type="text"
              value={configId}
              onChange={(e) => setConfigId(e.target.value)}
              placeholder="e.g. my_strategy_v1"
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm focus:border-[var(--color-primary)] focus:outline-none"
            />
          </div>

          {isLoading ? (
            <div className="flex items-center gap-2 py-8 justify-center text-sm text-[var(--color-text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading template...
            </div>
          ) : visibleFields.length === 0 ? (
            <p className="text-xs text-[var(--color-text-muted)] py-4">
              No template fields available. The config will be created with basic fields only.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-x-4 gap-y-3">
              {visibleFields.map((field) => (
                <div key={field.name}>
                  <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1 truncate" title={field.description || field.name}>
                    {field.name}
                  </label>
                  {field.type === "boolean" || field.type === "bool" ? (
                    <button
                      onClick={() => updateField(field.name, !fieldValues[field.name])}
                      className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
                        fieldValues[field.name]
                          ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                          : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)]"
                      }`}
                    >
                      {fieldValues[field.name] ? "true" : "false"}
                    </button>
                  ) : field.type === "number" || field.type === "float" || field.type === "int" || field.type === "integer" ? (
                    <input
                      type="number"
                      step="any"
                      value={String(fieldValues[field.name] ?? "")}
                      onChange={(e) => updateField(field.name, e.target.value ? Number(e.target.value) : undefined)}
                      className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm tabular-nums focus:border-[var(--color-primary)] focus:outline-none"
                    />
                  ) : (
                    <input
                      type="text"
                      value={String(fieldValues[field.name] ?? "")}
                      onChange={(e) => updateField(field.name, e.target.value)}
                      className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
                    />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-[var(--color-border)]">
          {createMutation.isError && (
            <span className="text-xs text-[var(--color-red)] mr-auto">
              {createMutation.error instanceof Error ? createMutation.error.message : "Failed"}
            </span>
          )}
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!configId.trim() || createMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-sm font-medium text-white transition-all disabled:opacity-40"
          >
            {createMutation.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
            Create Config
          </button>
        </div>
      </div>
    </>
  );
}

// ── Main Export ──

export function ControllersTab() {
  const { server } = useServer();
  const [search, setSearch] = useState("");
  const [newConfigTarget, setNewConfigTarget] = useState<{
    type: string;
    name: string;
  } | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server!),
    enabled: !!server,
  });

  const controllerTypes = data?.controller_types ?? {};

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

  const grouped = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const c of filtered) {
      const list = map.get(c.type) || [];
      list.push(c.name);
      map.set(c.type, list);
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
                onNewConfig={(t, n) => setNewConfigTarget({ type: t, name: n })}
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

      {/* New Config Dialog */}
      {newConfigTarget && (
        <NewConfigDialog
          server={server}
          controllerType={newConfigTarget.type}
          controllerName={newConfigTarget.name}
          onClose={() => setNewConfigTarget(null)}
        />
      )}
    </div>
  );
}
