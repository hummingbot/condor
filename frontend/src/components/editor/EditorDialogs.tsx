import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Code2, FileText, Loader2, Upload, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import yaml from "js-yaml";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { api, type ControllerConfigSummary } from "@/lib/api";

// ── Delete Confirm Dialog ──

export function DeleteConfirmDialog({
  server,
  target,
  onClose,
  onDeleted,
}: {
  server: string;
  target: { kind: "config"; configId: string } | { kind: "controller"; controllerType: string; controllerName: string };
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: () => {
      if (target.kind === "config") {
        return api.deleteControllerConfig(server, target.configId);
      }
      return api.deleteController(server, target.controllerType, target.controllerName);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
      onDeleted?.();
      onClose();
    },
  });

  const displayName =
    target.kind === "config" ? target.configId : target.controllerName;
  const typeLabel = target.kind === "config" ? "Config" : "Controller";

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl z-50 p-5">
        <h2 className="text-sm font-semibold mb-2">Delete {typeLabel}</h2>
        <p className="text-xs text-[var(--color-text-muted)] mb-4">
          Are you sure you want to delete{" "}
          <span className="font-mono font-medium text-[var(--color-text)]">
            {displayName}
          </span>
          ? This cannot be undone.
        </p>
        {deleteMutation.isError && (
          <div className="mb-3 rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
            {deleteMutation.error instanceof Error
              ? deleteMutation.error.message
              : "Delete failed"}
          </div>
        )}
        <div className="flex items-center justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)]"
          >
            Cancel
          </button>
          <button
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-red)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {deleteMutation.isPending && (
              <Loader2 className="h-3 w-3 animate-spin" />
            )}
            Delete
          </button>
        </div>
      </div>
    </>
  );
}

// ── Upload Dialog (Config .yml or Controller .py) ──

type UploadMode = "config" | "controller";

export function UploadDialog({
  server,
  controllerTypes,
  onClose,
}: {
  server: string;
  controllerTypes: Record<string, string[]>;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<UploadMode>("config");
  const [content, setContent] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // Controller-specific fields
  const typeOptions = Object.keys(controllerTypes);
  const [controllerType, setControllerType] = useState(typeOptions[0] ?? "");
  const [controllerName, setControllerName] = useState("");

  const validateContent = useCallback(
    (val: string, m: UploadMode) => {
      if (m === "config") {
        try {
          const parsed = yaml.load(val);
          if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
            return "YAML must be a mapping";
          }
          if (!(parsed as Record<string, unknown>).id) {
            return "Config must have an 'id' field";
          }
          return null;
        } catch (e) {
          return e instanceof Error ? e.message : "Invalid YAML";
        }
      }
      // Controller: just needs non-empty Python
      if (!val.trim()) return "Paste or drop a Python file";
      return null;
    },
    [],
  );

  const handleContentChange = useCallback(
    (val: string) => {
      setContent(val);
      setParseError(validateContent(val, mode));
    },
    [mode, validateContent],
  );

  const handleModeChange = useCallback(
    (m: UploadMode) => {
      setMode(m);
      setParseError(content ? validateContent(content, m) : null);
    },
    [content, validateContent],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (!file) return;

      const isPy = file.name.endsWith(".py");
      const isYml = file.name.endsWith(".yml") || file.name.endsWith(".yaml");

      if (isPy) {
        setMode("controller");
        // Auto-fill controller name from filename
        const name = file.name.replace(/\.py$/, "");
        setControllerName(name);
      } else if (!isYml) {
        return; // ignore unsupported files
      }

      const reader = new FileReader();
      reader.onload = (ev) => {
        const text = ev.target?.result as string;
        if (text) {
          setContent(text);
          setParseError(validateContent(text, isPy ? "controller" : "config"));
          if (isYml) setMode("config");
        }
      };
      reader.readAsText(file);
    },
    [validateContent],
  );

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (mode === "config") {
        const parsed = yaml.load(content) as Record<string, unknown>;
        const configId = String(parsed.id);
        await api.createControllerConfig(server, configId, parsed);
      } else {
        await api.updateControllerSource(server, controllerType, controllerName, content);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
      onClose();
    },
  });

  const canUpload =
    !parseError &&
    content.trim() &&
    !uploadMutation.isPending &&
    (mode === "config" || (controllerType && controllerName.trim()));

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />
      <div className="fixed inset-4 md:inset-auto md:top-1/2 md:left-1/2 md:-translate-x-1/2 md:-translate-y-1/2 md:w-[560px] md:max-h-[80vh] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-xl z-50 flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <h2 className="text-sm font-semibold">Upload</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-surface-hover)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Mode toggle */}
          <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-0.5 w-fit">
            <button
              onClick={() => handleModeChange("config")}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                mode === "config"
                  ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm"
                  : "text-[var(--color-text-muted)]"
              }`}
            >
              <FileText className="h-3 w-3" />
              Config (.yml)
            </button>
            <button
              onClick={() => handleModeChange("controller")}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                mode === "controller"
                  ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm"
                  : "text-[var(--color-text-muted)]"
              }`}
            >
              <Code2 className="h-3 w-3" />
              Controller (.py)
            </button>
          </div>

          {/* Controller metadata fields */}
          {mode === "controller" && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">
                  Type *
                </label>
                <select
                  value={controllerType}
                  onChange={(e) => setControllerType(e.target.value)}
                  className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm focus:border-[var(--color-primary)] focus:outline-none"
                >
                  {typeOptions.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">
                  Name *
                </label>
                <input
                  type="text"
                  value={controllerName}
                  onChange={(e) => setControllerName(e.target.value)}
                  placeholder="e.g. my_strategy"
                  className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm focus:border-[var(--color-primary)] focus:outline-none"
                />
              </div>
            </div>
          )}

          {/* Drop zone */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
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
              Drop a {mode === "config" ? ".yml" : ".py"} file here, or paste{" "}
              {mode === "config" ? "YAML" : "Python"} below
            </p>
          </div>

          <CodeEditor
            value={content}
            onChange={handleContentChange}
            language={mode === "config" ? "yaml" : "python"}
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
          {uploadMutation.isError && (
            <span className="text-xs text-[var(--color-red)] mr-auto">
              {uploadMutation.error instanceof Error
                ? uploadMutation.error.message
                : "Failed"}
            </span>
          )}
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)]"
          >
            Cancel
          </button>
          <button
            onClick={() => uploadMutation.mutate()}
            disabled={!canUpload}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {uploadMutation.isPending && (
              <Loader2 className="h-3 w-3 animate-spin" />
            )}
            Upload
          </button>
        </div>
      </div>
    </>
  );
}

// ── Clone Config Dialog ──

export function CloneConfigDialog({
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
      Object.entries(data.config).filter(([k]) => k !== "id"),
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
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-surface-hover)]"
          >
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
              {createMutation.error instanceof Error
                ? createMutation.error.message
                : "Failed"}
            </span>
          )}
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-sm text-[var(--color-text-muted)]"
          >
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!newId.trim() || !!yamlError || createMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {createMutation.isPending && (
              <Loader2 className="h-3 w-3 animate-spin" />
            )}
            Clone
          </button>
        </div>
      </div>
    </>
  );
}

// ── New Config Dialog (with controller picker) ──

export function NewConfigDialog({
  server,
  controllerTypes,
  initialControllerType,
  initialControllerName,
  onClose,
}: {
  server: string;
  controllerTypes: Record<string, string[]>;
  initialControllerType?: string;
  initialControllerName?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [configId, setConfigId] = useState("");
  const [fieldValues, setFieldValues] = useState<Record<string, unknown>>({});

  // Controller selection
  const typeOptions = Object.keys(controllerTypes);
  const [selectedType, setSelectedType] = useState(initialControllerType ?? typeOptions[0] ?? "");
  const namesForType = controllerTypes[selectedType] ?? [];
  const [selectedName, setSelectedName] = useState(initialControllerName ?? namesForType[0] ?? "");

  // When type changes, reset name to first available
  const handleTypeChange = useCallback(
    (type: string) => {
      setSelectedType(type);
      const names = controllerTypes[type] ?? [];
      setSelectedName(names[0] ?? "");
      setFieldValues({});
    },
    [controllerTypes],
  );

  const { data: template, isLoading } = useQuery({
    queryKey: ["controller-template", server, selectedType, selectedName],
    queryFn: () => api.getControllerConfigTemplate(server, selectedType, selectedName),
    enabled: !!selectedType && !!selectedName,
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> = {
        ...hiddenDefaults,
        ...fieldValues,
        controller_name: selectedName,
        controller_type: selectedType,
      };
      return api.createControllerConfig(server, configId, config);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["available-configs", server] });
      onClose();
    },
  });

  const HIDDEN_FIELDS = new Set([
    "id",
    "controller_name",
    "controller_type",
    "manual_kill_switch",
    "initial_positions",
  ]);

  const { visibleFields, hiddenDefaults } = useMemo(() => {
    if (!template)
      return { visibleFields: [], hiddenDefaults: {} as Record<string, unknown> };
    const raw = template.fields ?? template;
    let allFields: { name: string; type: string; default: unknown; description?: string }[];
    if (Array.isArray(raw)) {
      allFields = raw as typeof allFields;
    } else {
      allFields = Object.entries(raw).map(([name, info]) => {
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
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)]">
          <h2 className="text-sm font-semibold">New Config</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-surface-hover)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Controller picker */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">
                Controller Type
              </label>
              <select
                value={selectedType}
                onChange={(e) => handleTypeChange(e.target.value)}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm focus:border-[var(--color-primary)] focus:outline-none"
              >
                {typeOptions.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1 uppercase tracking-wide">
                Controller
              </label>
              <select
                value={selectedName}
                onChange={(e) => { setSelectedName(e.target.value); setFieldValues({}); }}
                className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm focus:border-[var(--color-primary)] focus:outline-none"
              >
                {namesForType.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Config ID */}
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
                  <label
                    className="block text-xs font-medium text-[var(--color-text-muted)] mb-1 truncate"
                    title={field.description || field.name}
                  >
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
                  ) : field.type === "number" ||
                    field.type === "float" ||
                    field.type === "int" ||
                    field.type === "integer" ? (
                    <input
                      type="number"
                      step="any"
                      value={String(fieldValues[field.name] ?? "")}
                      onChange={(e) =>
                        updateField(field.name, e.target.value ? Number(e.target.value) : undefined)
                      }
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

        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-[var(--color-border)]">
          {createMutation.isError && (
            <span className="text-xs text-[var(--color-red)] mr-auto">
              {createMutation.error instanceof Error
                ? createMutation.error.message
                : "Failed"}
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
            disabled={!configId.trim() || !selectedName || createMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-4 py-1.5 text-sm font-medium text-white transition-all disabled:opacity-40"
          >
            {createMutation.isPending && (
              <Loader2 className="h-3 w-3 animate-spin" />
            )}
            Create Config
          </button>
        </div>
      </div>
    </>
  );
}
