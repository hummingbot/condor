import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Plus, Server, Trash2, X } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import type { CustomProvider } from "@/lib/api";

/**
 * Manage OpenAI-compatible LLM endpoints (Venice AI, Together, Fireworks, a
 * local vLLM/LM Studio server, ...).
 *
 * These are the same records the Telegram `/agent → Change LLM → Custom
 * endpoint` flow writes, so an endpoint added here is immediately usable in
 * the bot and vice versa. The API key is stored on the bot's host and is never
 * sent back to the browser — the server only reports whether one is set.
 */
export function CustomProvidersSettings() {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["custom-providers"],
    queryFn: () => api.getCustomProviders(),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["custom-providers"] });
    // The chat model dropdown reads the same list through `useSessionOptions`,
    // which caches with `staleTime: Infinity` — without this it would never
    // pick up an added or forgotten endpoint short of a page reload.
    qc.invalidateQueries({ queryKey: ["session-options"] });
  };

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteCustomProvider(name),
    onSuccess: () => {
      setConfirmDelete(null);
      invalidate();
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  const providers = data?.providers ?? [];

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">
          <Server className="mr-1.5 inline h-4 w-4" />
          Custom LLM Endpoints
        </h3>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          Connect any OpenAI-compatible API — Venice AI, Together, Fireworks, or
          your own vLLM / LM Studio server. Saved endpoints appear in the chat
          model picker and in the Telegram bot.
        </p>

        {providers.length === 0 && !adding && (
          <p className="rounded-lg border border-dashed border-[var(--color-border)] px-3 py-6 text-center text-xs text-[var(--color-text-muted)]">
            No endpoints yet.
          </p>
        )}

        <div className="space-y-2">
          {providers.map((p) => (
            <ProviderRow
              key={p.name}
              provider={p}
              confirming={confirmDelete === p.name}
              deleting={remove.isPending && confirmDelete === p.name}
              onRequestDelete={() => setConfirmDelete(p.name)}
              onCancelDelete={() => setConfirmDelete(null)}
              onConfirmDelete={() => remove.mutate(p.name)}
            />
          ))}
        </div>

        {remove.isError && (
          <p className="mt-2 text-xs text-[var(--color-red)]">
            Failed to remove: {(remove.error as Error).message}
          </p>
        )}
      </section>

      {adding ? (
        <AddProviderForm
          onDone={() => {
            setAdding(false);
            invalidate();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm font-medium text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          <Plus className="h-4 w-4" />
          Add endpoint
        </button>
      )}
    </div>
  );
}

function ProviderRow({
  provider,
  confirming,
  deleting,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  provider: CustomProvider;
  confirming: boolean;
  deleting: boolean;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-[var(--color-border)] px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-[var(--color-text)]">
          {provider.name}
        </div>
        <div className="truncate text-xs text-[var(--color-text-muted)]">
          {provider.base_url}
          {" · "}
          {provider.has_key ? "API key saved" : "no API key"}
        </div>
      </div>

      {confirming ? (
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={onConfirmDelete}
            disabled={deleting}
            className="rounded px-2 py-1 text-xs font-medium text-[var(--color-red)] hover:bg-[var(--color-red)]/10 disabled:opacity-50"
          >
            {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Forget"}
          </button>
          <button
            onClick={onCancelDelete}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            aria-label="Cancel"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <button
          onClick={onRequestDelete}
          className="shrink-0 rounded p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)]"
          aria-label={`Forget ${provider.name}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

function AddProviderForm({
  onDone,
  onCancel,
}: {
  onDone: () => void;
  onCancel: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [name, setName] = useState("");
  const [result, setResult] = useState<{ name: string; count: number } | null>(null);

  // The server validates by fetching {base_url}/models, so a success here
  // means the URL, the key and the response shape are all good.
  const add = useMutation({
    mutationFn: () =>
      api.addCustomProvider({
        base_url: baseUrl,
        api_key: apiKey || undefined,
        name: name || undefined,
      }),
    onSuccess: (res) => setResult({ name: res.provider.name, count: res.models.length }),
  });

  if (result) {
    return (
      <section className="space-y-3 rounded-lg border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5 px-3 py-3">
        <p className="flex items-center gap-2 text-sm text-[var(--color-text)]">
          <Check className="h-4 w-4 text-[var(--color-primary)]" />
          Saved <span className="font-medium">{result.name}</span> — {result.count}{" "}
          chat models available.
        </p>
        <p className="text-xs text-[var(--color-text-muted)]">
          Pick one from the model dropdown when you start a new chat.
        </p>
        <button
          onClick={onDone}
          className="rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-sm font-medium text-white transition-opacity hover:opacity-90"
        >
          Done
        </button>
      </section>
    );
  }

  return (
    <section className="space-y-3 rounded-lg border border-[var(--color-border)] px-3 py-3">
      <div>
        <label className="text-xs font-medium text-[var(--color-text-muted)]">
          Base URL
        </label>
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.venice.ai/api/v1"
          autoFocus
          className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        />
      </div>

      <div>
        <label className="text-xs font-medium text-[var(--color-text-muted)]">
          API key <span className="font-normal">(leave blank if not required)</span>
        </label>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="sk-..."
          autoComplete="off"
          className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        />
      </div>

      <div>
        <label className="text-xs font-medium text-[var(--color-text-muted)]">
          Name <span className="font-normal">(optional — derived from the URL)</span>
        </label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Venice"
          className="mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        />
      </div>

      {add.isError && (
        <p className="text-xs text-[var(--color-red)]">
          {(add.error as Error).message}
        </p>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => add.mutate()}
          disabled={!baseUrl.trim() || add.isPending}
          className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {add.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Checking endpoint
            </>
          ) : (
            "Validate & save"
          )}
        </button>
        <button
          onClick={onCancel}
          className="rounded-lg border border-[var(--color-border)] px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          Cancel
        </button>
      </div>
    </section>
  );
}
