import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Circle,
  Loader2,
  Pencil,
  RotateCcw,
  Save,
} from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import yaml from "js-yaml";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

export function BotDetail() {
  const { id } = useParams<{ id: string }>();
  const { server } = useServer();
  const queryClient = useQueryClient();

  const [editing, setEditing] = useState(false);
  const [yamlValue, setYamlValue] = useState("");
  const [originalYaml, setOriginalYaml] = useState("");
  const [yamlError, setYamlError] = useState<string | null>(null);
  const prevConfigSig = useRef("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["bot", server, id],
    queryFn: () => api.getBot(server!, id!),
    enabled: !!server && !!id,
    refetchInterval: 10000,
  });

  // Derive config_id from config dict
  const configId = data?.config
    ? String(
        (data.config as Record<string, unknown>).config_base_name ??
          (data.config as Record<string, unknown>).id ??
          "",
      )
    : "";

  // Sync config → YAML when data changes (only if not dirty)
  useMemo(() => {
    if (!data?.config || Object.keys(data.config).length === 0) return;
    const sig = JSON.stringify(data.config);
    if (sig === prevConfigSig.current) return;
    prevConfigSig.current = sig;
    const filtered = Object.fromEntries(
      Object.entries(data.config).filter(([k]) => k !== "id"),
    );
    const dumped = yaml.dump(filtered, { sortKeys: false, lineWidth: -1 });
    setYamlValue(dumped);
    setOriginalYaml(dumped);
    setYamlError(null);
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
  const canSave = isDirty && !yamlError && !!configId;

  const saveMutation = useMutation({
    mutationFn: () => api.updateConfigYaml(server!, configId, yamlValue),
    onSuccess: () => {
      setOriginalYaml(yamlValue);
      prevConfigSig.current = ""; // force re-sync on next fetch
      queryClient.invalidateQueries({ queryKey: ["bot", server, id] });
    },
  });

  const handleSave = () => saveMutation.mutate();
  const handleReset = () => {
    setYamlValue(originalYaml);
    setYamlError(null);
    saveMutation.reset();
  };
  const handleToggleEdit = () => {
    if (editing && isDirty) {
      handleReset();
    }
    setEditing(!editing);
  };

  if (!server || !id) return null;
  if (isLoading) return <p className="text-[var(--color-text-muted)]">Loading...</p>;
  if (error)
    return (
      <p className="text-[var(--color-red)]">
        {error instanceof Error ? error.message : "Error"}
      </p>
    );
  if (!data) return null;

  const { bot, config, performance } = data;
  const statusColor =
    bot.status === "running"
      ? "text-[var(--color-green)]"
      : "text-[var(--color-red)]";
  const hasConfig = Object.keys(config).length > 0;

  return (
    <div>
      <Link
        to="/bots"
        className="mb-4 inline-flex items-center gap-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to bots
      </Link>

      <div className="mb-6 flex items-center gap-3">
        <h2 className="text-xl font-bold">{bot.name}</h2>
        <span className={`flex items-center gap-1.5 text-sm ${statusColor}`}>
          <Circle className="h-2 w-2 fill-current" />
          {bot.status}
        </span>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Config */}
        <div
          className={`rounded-lg border bg-[var(--color-surface)] p-4 transition-colors ${
            isDirty
              ? "border-[var(--color-warning)]/60"
              : "border-[var(--color-border)]"
          }`}
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 className="font-medium text-[var(--color-text-muted)]">
              Configuration
            </h3>
            {hasConfig && configId && (
              <button
                onClick={handleToggleEdit}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors ${
                  editing
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <Pencil className="h-3 w-3" />
                {editing ? "Editing" : "Edit"}
              </button>
            )}
          </div>

          {!hasConfig ? (
            <p className="text-sm text-[var(--color-text-muted)]">
              No config available
            </p>
          ) : editing ? (
            <>
              {/* Save / Reset toolbar */}
              <div className="mb-2 flex items-center justify-end gap-2">
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

              {yamlError && (
                <div className="mb-2 flex items-start gap-2 rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  <span className="break-all">{yamlError}</span>
                </div>
              )}
              {saveMutation.isError && (
                <div className="mb-2 rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
                  {saveMutation.error instanceof Error
                    ? saveMutation.error.message
                    : "Save failed"}
                </div>
              )}

              <CodeEditor
                value={yamlValue}
                onChange={handleYamlChange}
                language="yaml"
                height="400px"
              />
            </>
          ) : (
            <dl className="space-y-2 text-sm">
              {Object.entries(config).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <dt className="text-[var(--color-text-muted)]">{k}</dt>
                  <dd className="font-mono">{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>

        {/* Performance */}
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 font-medium text-[var(--color-text-muted)]">
            Performance
          </h3>
          {Object.keys(performance).length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)]">
              No performance data
            </p>
          ) : (
            <dl className="space-y-2 text-sm">
              {Object.entries(performance).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <dt className="text-[var(--color-text-muted)]">{k}</dt>
                  <dd className="font-mono">{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </div>
    </div>
  );
}
