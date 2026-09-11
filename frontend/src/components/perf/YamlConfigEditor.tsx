import { useMutation } from "@tanstack/react-query";
import { ChevronRight, Loader2, RotateCcw, Save } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import yamlLib from "js-yaml";

import { CodeEditor } from "@/components/editor/CodeEditor";
import { api } from "@/lib/api";
import { configToYaml, CONTROLLER_HIDDEN_KEYS } from "@/lib/configYaml";

/**
 * The right drawer's config column: a controller's config as editable YAML.
 *
 * Lifted out of `PerfBrowser` unchanged (ARCH-300). It depends on nothing in
 * the browser but its props — it owns its own dump, buffer, parse state and
 * save mutation — which is what makes it a module rather than 130 lines in the
 * middle of a 2,700-line one.
 */
export function YamlConfigEditor({
  config,
  server,
  configId,
  botName,
  onSaved,
  onCollapse,
}: {
  config: Record<string, unknown>;
  server: string;
  configId: string;
  botName: string;
  onSaved: () => void;
  onCollapse: () => void;
}) {
  // Memoize the dump so typing / local-state renders don't re-run yaml.dump, and
  // so WS-tick churn of the `config` object identity that yields the SAME content
  // produces an identical string (compared by value) below.
  const originalYaml = useMemo(
    () =>
      configToYaml(config, {
        hiddenKeys: CONTROLLER_HIDDEN_KEYS,
        stripUnderscore: true,
        sortKeys: true,
      }),
    [config],
  );
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
        <div className="flex items-center gap-2 min-w-0">
          <button
            onClick={onCollapse}
            className="rounded p-0.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title="Hide config"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Config
          </h3>
          {isDirty && (
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-yellow)]" title="Unsaved changes" />
          )}
        </div>
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
