import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, BookOpen, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { MarkdownEditor } from "@/components/agent/AgentOverviewTab";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { api } from "@/lib/api";

// ── Strategy (Playbook) Detail Page ──
//
// A strategy is a pure playbook template (refactor-01b): strategy.md (the tick
// tactic) + default_config. ALL operational history — sessions, experiments,
// learnings, live instances — lives at the agent level (/agents/:slug), where
// this playbook's sessions are just a filter.

function DefaultConfigEditor({
  slug,
  sslug,
  config,
}: {
  slug: string;
  sslug: string;
  config: Record<string, unknown>;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(() => JSON.stringify(config, null, 2));
  const [dirty, setDirty] = useState(false);
  const [parseError, setParseError] = useState("");

  // Re-sync from the server when not mid-edit.
  useEffect(() => {
    if (!dirty) setValue(JSON.stringify(config, null, 2));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(config)]);

  const saveMut = useMutation({
    mutationFn: (parsed: Record<string, unknown>) =>
      api.updateStrategyConfig(slug, sslug, parsed),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
      setDirty(false);
    },
  });

  const handleSave = () => {
    try {
      const parsed = JSON.parse(value) as Record<string, unknown>;
      setParseError("");
      saveMut.mutate(parsed);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Default Config
          </span>
          <span className="ml-2 text-[10px] text-[var(--color-text-muted)]">
            launch defaults for sessions of this playbook (JSON)
          </span>
        </div>
        <button
          onClick={handleSave}
          disabled={!dirty || saveMut.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-all disabled:opacity-30"
        >
          <Save className="h-3.5 w-3.5" />
          {saveMut.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      {(parseError || saveMut.isError) && (
        <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {parseError ||
            (saveMut.error instanceof Error ? saveMut.error.message : "Save failed")}
        </div>
      )}
      <textarea
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setDirty(true);
        }}
        spellCheck={false}
        className="min-h-[300px] w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 font-mono text-sm leading-relaxed text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]/50"
      />
    </div>
  );
}

export function StrategyDetail() {
  const { slug, sslug } = useParams<{ slug: string; sslug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug!, sslug!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      navigate(`/agents/${slug}`);
    },
  });

  const { data: playbook, isLoading, error } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug!, sslug!),
    enabled: !!slug && !!sslug,
  });

  if (error && !playbook) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="max-w-sm rounded-lg border border-red-500/30 bg-[var(--color-surface)] p-8 text-center">
          <AlertCircle className="mx-auto mb-3 h-10 w-10 text-[var(--color-red)]" />
          <h2 className="mb-1 text-lg font-semibold">Failed to Load Strategy</h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            {error instanceof Error ? error.message : "An unexpected error occurred."}
          </p>
          <button
            onClick={() => navigate(`/agents/${slug}`)}
            className="mt-4 inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Back to Agent
          </button>
        </div>
      </div>
    );
  }

  if (isLoading || !playbook) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-4">
        <button
          onClick={() => navigate(`/agents/${slug}`)}
          className="mb-3 flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Agent
        </button>

        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="flex items-center gap-2 text-xl font-bold text-[var(--color-text)]">
              <BookOpen className="h-5 w-5 text-[var(--color-text-muted)]" />
              <span className="text-[var(--color-text-muted)]">{slug}</span>
              <span className="text-[var(--color-text-muted)]">/</span>
              {playbook.name}
            </h1>
            {playbook.description && (
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">{playbook.description}</p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
              <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
                {playbook.slug}
              </span>
              {playbook.agent_key && (
                <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono text-[var(--color-primary)]">
                  {playbook.agent_key}
                </span>
              )}
              <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
                {playbook.session_count} session{playbook.session_count !== 1 ? "s" : ""} — view in the agent's history
              </span>
            </div>
          </div>
          <button
            onClick={() => setShowDeleteConfirm(true)}
            className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20"
            title="Delete strategy"
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Delete</span>
          </button>
        </div>
      </div>

      {/* Playbook editor + default config */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <MarkdownEditor
          label="Playbook"
          sublabel="strategy.md — the tick tactic (frontmatter + body)"
          content={playbook.strategy_md}
          onSave={(value) => api.updateStrategyMd(slug!, sslug!, value)}
          invalidateKey={["strategy", slug, sslug]}
        />
        <DefaultConfigEditor
          slug={slug!}
          sslug={sslug!}
          config={playbook.default_config}
        />
      </div>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Strategy"
        isPending={deleteMutation.isPending}
        isError={deleteMutation.isError}
        errorText="Failed to delete strategy. It may have a running session."
        onConfirm={() => deleteMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete <strong className="text-[var(--color-text)]">{playbook.name}</strong>? Its
        past sessions stay in the agent's history. This cannot be undone.
      </ConfirmDialog>
    </div>
  );
}
