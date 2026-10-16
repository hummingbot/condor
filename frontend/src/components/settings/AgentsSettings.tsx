import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Download, Loader2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";

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
