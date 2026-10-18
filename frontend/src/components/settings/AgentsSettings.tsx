import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Check, Download, Globe, Loader2, Upload } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";

import { HubBrowser } from "./HubBrowser";
import { ImportAgentDialog } from "./ImportAgentDialog";

/**
 * Where an agent arrives from somewhere else, and where one leaves.
 *
 * It needs a home of its own rather than a button hung off an existing one:
 * the dashboard has no "New Agent" affordance at all — agents are created
 * through chat — so there was nowhere to put Import. Export is also offered on
 * each agent's own workspace header, beside Delete; this panel is the place
 * that answers "how do I move an agent" without first knowing which one.
 */
export function AgentsSettings() {
  const queryClient = useQueryClient();
  const { data: agents, isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
  });

  return (
    <div className="space-y-8">
      <section>
        <h2 className="mb-1 flex items-center gap-1.5 text-sm font-bold text-[var(--color-text)]">
          <Globe className="h-3.5 w-3.5" />
          The agent hub
        </h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          Agents other people published. Installing one goes through the same
          review as a file: every executable is named before anything is written,
          the routines and skills arrive switched off, and nothing is started.
        </p>
        <HubBrowser
          onInstalled={() => queryClient.invalidateQueries({ queryKey: ["agents"] })}
        />
      </section>

      <section className="border-t border-[var(--color-border)] pt-8">
        <h2 className="mb-1 text-sm font-bold text-[var(--color-text)]">
          Import an agent
        </h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          An agent travels as one file. Importing reads it, shows you what is in it and
          registers the agent with its routines and skills switched off — nothing runs
          until you switch them on.
        </p>
        <ImportAgentDialog
          onImported={() => queryClient.invalidateQueries({ queryKey: ["agents"] })}
        />
      </section>

      <section className="border-t border-[var(--color-border)] pt-8">
        <h2 className="mb-1 text-sm font-bold text-[var(--color-text)]">
          Export an agent
        </h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          The file carries the agent itself — its AGENT.md, skills, routines, controllers
          and loops. Its memory, journals, session history and this install's server pin
          stay here.
        </p>
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />
        ) : (
          <ul className="space-y-1.5">
            {(agents ?? []).map((agent) => (
              <li
                key={agent.slug}
                className="flex items-center justify-between gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Brain className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                  <span className="truncate text-sm text-[var(--color-text)]">
                    {agent.name}
                  </span>
                  <span className="truncate font-mono text-[11px] text-[var(--color-text-muted)]">
                    {agent.slug}
                  </span>
                </span>
                <ExportAgentButton slug={agent.slug} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="border-t border-[var(--color-border)] pt-8">
        <h2 className="mb-1 text-sm font-bold text-[var(--color-text)]">
          Publishing to the hub
        </h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          Publishing needs a Botcamp token, which is install-wide: every publish
          from this box is made with it, under that account. Paste one here, then
          use Publish on an agent's own page. Publishing sends the agent's files
          off this box — unpublishing removes the listing, it does not recall
          anybody's download.
        </p>
        <HubTokenField />
      </section>
    </div>
  );
}

/**
 * The install's Botcamp token: set it, replace it, clear it.
 *
 * It is never read back. There is no reveal control and no route behind one —
 * the GET answers "set" or "not set" and nothing else, because a second copy
 * of the credential on a screen buys no capability the operator does not
 * already have. They can paste a new one. Same posture as
 * `ApiKeysSettings` takes with an exchange key.
 */
export function HubTokenField() {
  const queryClient = useQueryClient();
  const [value, setValue] = useState("");
  const { data } = useQuery({ queryKey: ["hub-token"], queryFn: api.getHubToken });

  const save = useMutation({
    mutationFn: (token: string) => api.setHubToken(token),
    onSuccess: () => {
      setValue("");
      queryClient.invalidateQueries({ queryKey: ["hub-token"] });
    },
  });

  const locked = data ? !data.can_change : false;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <input
          type="password"
          aria-label="Botcamp token"
          value={value}
          disabled={locked || save.isPending}
          onChange={(e) => setValue(e.target.value)}
          placeholder={data?.set ? "A token is set — paste one to replace it" : "Paste your token"}
          className="w-72 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 font-mono text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none disabled:opacity-50"
        />
        <button
          onClick={() => save.mutate(value)}
          disabled={locked || save.isPending || !value.trim()}
          className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
        {data?.set && !locked && (
          <button
            onClick={() => save.mutate("")}
            disabled={save.isPending}
            className="text-xs text-[var(--color-text-muted)] underline hover:text-[var(--color-red)] disabled:opacity-50"
          >
            Clear
          </button>
        )}
      </div>
      {data?.set && (
        <p data-hub-token-set className="flex items-center gap-1 text-[11px] text-[var(--color-text-muted)]">
          <Check className="h-3 w-3 text-[var(--color-primary)]" /> A token is set.
          It is never shown again — replace it by pasting another.
        </p>
      )}
      {locked && (
        <p className="text-[11px] text-[var(--color-text-muted)]">
          Only the admin can change it: there is one token for the whole install.
        </p>
      )}
      {save.isError && (
        <p role="alert" data-hub-token-error className="text-xs text-[var(--color-red)]">
          {save.error.message}
        </p>
      )}
    </div>
  );
}

/**
 * Download one agent's bundle.
 *
 * The endpoint is bearer-guarded, so this fetches the text and makes an object
 * URL rather than pointing an `<a href>` at it — a link cannot carry the
 * header. The URL is revoked straight after the click, since the blob is only
 * needed for the instant the browser takes to save it.
 */
export function ExportAgentButton({ slug }: { slug: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const text = await api.exportAgentBundle(slug);
      const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${slug}.condor-agent.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex items-center gap-2">
      {error && (
        <span role="alert" data-export-error className="text-xs text-[var(--color-red)]">
          {error}
        </span>
      )}
      <button
        onClick={download}
        disabled={busy}
        title={`Export ${slug} as one file`}
        className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)] disabled:opacity-50"
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Download className="h-3.5 w-3.5" />
        )}
        Export
      </button>
    </span>
  );
}

/**
 * Publish one agent to the hub, after showing what will be uploaded.
 *
 * Beside Export because it is the same act with a different destination — and
 * the destination is the whole difference, which is why this one confirms and
 * Export does not: a download stays on this box, and a publish cannot be
 * unpublished out of somebody else's copy.
 *
 * **The preview is the export call.** It fetches `GET /agents/{slug}/bundle` —
 * the identical bytes the publish will send, built by the identical function —
 * and lists what is in them. That is not only "the same preview" in spirit: it
 * means FEAT-144's refusal for a value that can only be a secret surfaces
 * here, before the Publish button is ever offered, because `build` is what
 * refuses and both doors call it.
 */
export function PublishAgentButton({ slug }: { slug: string }) {
  const [preview, setPreview] = useState<{ files: string[]; bytes: number; version: number } | null>(
    null,
  );
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function look() {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const text = await api.exportAgentBundle(slug);
      const bundle = JSON.parse(text) as {
        version: number;
        files: { path: string }[];
      };
      setPreview({
        files: bundle.files.map((f) => f.path),
        bytes: new TextEncoder().encode(text).length,
        version: bundle.version,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.publishAgentToHub(slug);
      setPreview(null);
      setDone(String(result.receipt.listing_id ?? ""));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex items-center gap-2">
      {error && (
        <span role="alert" data-publish-error className="text-xs text-[var(--color-red)]">
          {error}
        </span>
      )}
      {done !== null && (
        <span data-publish-done className="text-xs text-[var(--color-text-muted)]">
          Published{done && ` as ${done}`}.
        </span>
      )}
      {preview ? (
        <span
          data-publish-preview
          className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-2.5 py-1 text-[11px] text-[var(--color-text-muted)]"
        >
          <span>
            Uploading version {preview.version} — {preview.files.length} file
            {preview.files.length !== 1 ? "s" : ""}, {preview.bytes} bytes. This
            leaves your machine and cannot be recalled from anybody's download.
          </span>
          <button
            onClick={send}
            disabled={busy}
            className="shrink-0 rounded border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 px-2 py-0.5 font-semibold text-[var(--color-primary)] disabled:opacity-50"
          >
            {busy ? "Publishing…" : "Publish"}
          </button>
          <button
            onClick={() => setPreview(null)}
            disabled={busy}
            className="shrink-0 underline hover:text-[var(--color-text)] disabled:opacity-50"
          >
            Cancel
          </button>
        </span>
      ) : (
        <button
          onClick={look}
          disabled={busy}
          title={`Publish ${slug} to the agent hub`}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)] disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Upload className="h-3.5 w-3.5" />
          )}
          Publish
        </button>
      )}
    </span>
  );
}
