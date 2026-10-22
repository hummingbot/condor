import { useMutation } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";
import { useState } from "react";

import {
  api,
  errorStatus,
  type AgentImportPlan,
  type AgentUpdatePlan,
  type HubCatalogue,
} from "@/lib/api";

import { type BundleSource } from "./bundleSource";
import { ImportAgentDialog } from "./ImportAgentDialog";

/**
 * A hub agent, as the four calls the review dialog makes (FEAT-146).
 *
 * The dialog is reused rather than reimplemented, and this is the whole of the
 * difference between a file on disk and an agent pulled from Botcamp. The
 * reason is not economy: the review *is* the sandbox, so a second review
 * screen would be a second chance to omit the executables list, the findings
 * or the muted-on-arrival sentence — and the screen that got it wrong would be
 * the one the operator actually used.
 *
 * Both `install` and `applyUpdate` post to the same endpoint, because the
 * server decides which verb it is from the bundle's identity and not from
 * which button was pressed. Each re-fetches rather than reusing the preview's
 * bytes; what makes that safe is the integrity check on the server, which
 * holds both fetches to the download's own header.
 */
function hubSource(slug: string, version: string): BundleSource {
  return {
    key: `hub:${slug}:${version}`,
    preview: async (asSlug) => {
      const res = await api.previewHubInstall(slug, asSlug, version);
      return res.verb === "update"
        ? { verb: "update", plan: res.plan as AgentUpdatePlan }
        : { verb: "install", plan: res.plan as AgentImportPlan };
    },
    install: async (asSlug) =>
      (await api.installFromHub(slug, asSlug, version)).plan as AgentImportPlan,
    previewUpdate: async () =>
      (await api.previewHubInstall(slug, "", version)).plan as AgentUpdatePlan,
    applyUpdate: async () =>
      (await api.installFromHub(slug, "", version)).plan as AgentUpdatePlan,
  };
}

/**
 * Pull an agent from Botcamp by slug, and install it, without leaving Condor.
 *
 * Mounted on `/hub`'s Pull agents section (FEAT-153) once `AgentHub` has
 * confirmed this install is linked to Botcamp — the backend's own read
 * endpoints still accept an anonymous lookup (PR #111's contract for a free
 * agent), but the dashboard does not reach this component until sign-in,
 * admin or not. Botcamp (PR #111) ships no browse or search at all,
 * only a lookup by a slug the operator must already have — pasted from a
 * Botcamp agent page, say — so this is that lookup, not a catalogue grid. It
 * answers the same question Import does — how does an agent get here — and
 * shares its review screen deliberately: a Botcamp agent is not a safer kind
 * of bundle, it is the same kind with a provenance label attached, and a
 * second review screen would imply otherwise.
 */
export function HubBrowser({ onInstalled }: { onInstalled: () => void }) {
  const [slug, setSlug] = useState("");
  const [looked, setLooked] = useState("");
  const [chosen, setChosen] = useState<HubCatalogue["listing"] | null>(null);

  const lookup = useMutation({
    mutationFn: (value: string) => api.getHubListing(value),
  });

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--color-text-muted)]">
        Botcamp does not serve a browsable catalogue yet — paste the slug of
        an agent someone shared, and Condor will fetch it directly.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const value = slug.trim();
          if (!value) return;
          setLooked(value);
          setChosen(null);
          lookup.mutate(value);
        }}
        className="flex items-center gap-2"
      >
        <input
          aria-label="Botcamp agent slug"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          placeholder="Agent slug, e.g. river_scalper"
          className="w-64 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        />
        <button
          type="submit"
          disabled={lookup.isPending || !slug.trim()}
          className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          <Search className="h-3.5 w-3.5" />
          Look up
        </button>
      </form>

      {lookup.isPending && (
        <p className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" /> Asking Botcamp…
        </p>
      )}

      {/* The server already tells apart "not found", "needs a membership or
          purchase" and "sign in" — this renders that sentence rather than a
          generic failure. */}
      {lookup.isError && (
        <p
          role="alert"
          data-hub-error
          data-hub-error-status={errorStatus(lookup.error)}
          className="text-sm text-[var(--color-red)]"
        >
          {(lookup.error as Error).message}{" "}
          <button
            onClick={() => lookup.mutate(looked)}
            className="underline hover:text-[var(--color-text)]"
          >
            Try again
          </button>
        </p>
      )}

      {lookup.isSuccess && (
        <div
          data-hub-listing={lookup.data.listing.slug}
          className="flex items-start justify-between gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2"
        >
          <span className="min-w-0">
            <span className="truncate text-sm font-semibold text-[var(--color-text)]">
              {lookup.data.listing.name || lookup.data.listing.slug}
            </span>
            <span className="block truncate text-xs text-[var(--color-text-muted)]">
              {lookup.data.listing.description || "No description."}
            </span>
            <span className="block text-[11px] text-[var(--color-text-muted)]">
              <span className="font-mono">{lookup.data.listing.slug}</span> ·
              latest version {lookup.data.listing.latest}
            </span>
          </span>
          <button
            onClick={() => setChosen(lookup.data.listing)}
            className="shrink-0 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
          >
            Review
          </button>
        </div>
      )}

      {/* The same dialog the file path uses, with the agent's provenance
          above it. Keyed on the slug so choosing a second one re-previews
          from scratch rather than showing the first one's plan. */}
      {chosen && (
        <ImportAgentDialog
          key={chosen.slug}
          source={hubSource(chosen.slug, "latest")}
          onImported={onInstalled}
          provenance={
            <div
              data-hub-provenance
              className="space-y-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5"
            >
              <span className="text-sm font-semibold text-[var(--color-text)]">
                {chosen.name || chosen.slug}
              </span>
              <p className="text-[11px] text-[var(--color-text-muted)]">
                {lookup.data?.trust}
              </p>
            </div>
          }
        />
      )}
    </div>
  );
}
