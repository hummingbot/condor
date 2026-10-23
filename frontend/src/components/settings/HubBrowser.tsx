import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { BadgeCheck, Loader2, Lock, Search } from "lucide-react";
import { useState } from "react";

import {
  api,
  errorStatus,
  HUB_CATALOGUE_PAGE_SIZE,
  type AgentImportPlan,
  type AgentUpdatePlan,
  type HubCatalogueEntry,
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

/** What the review dialog is opened on: one agent, by the slug a pull addresses. */
interface Chosen {
  slug: string;
  name: string;
  trust: string;
}

/**
 * Pull an agent from Botcamp, and install it, without leaving Condor.
 *
 * Mounted on `/hub`'s Pull agents section (FEAT-153) once `AgentHub` has
 * confirmed this install is linked to Botcamp — Botcamp never serves agent
 * code without a token, so there is nothing to pull before sign-in. Botcamp's
 * catalogue lists what is published, newest first; a private agent someone
 * shared is not in it, so the lookup by slug stays underneath. Either way it
 * answers the same question Import does — how does an agent get here — and
 * shares its review screen deliberately: a Botcamp agent is not a safer kind
 * of bundle, it is the same kind with a provenance label attached, and a
 * second review screen would imply otherwise.
 */
export function HubBrowser({ onInstalled }: { onInstalled: () => void }) {
  const [slug, setSlug] = useState("");
  const [looked, setLooked] = useState("");
  const [chosen, setChosen] = useState<Chosen | null>(null);

  const lookup = useMutation({
    mutationFn: (value: string) => api.getHubListing(value),
  });

  return (
    <div className="space-y-4">
      <Catalogue onReview={setChosen} />

      <p className="border-t border-[var(--color-border)] pt-4 text-xs text-[var(--color-text-muted)]">
        Got the slug of an agent someone shared? Private agents aren't listed
        above — look one up directly.
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
            onClick={() =>
              setChosen({
                slug: lookup.data.listing.slug,
                name: lookup.data.listing.name,
                trust: lookup.data.trust,
              })
            }
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
                {chosen.trust}
              </p>
            </div>
          }
        />
      )}
    </div>
  );
}

/**
 * Botcamp's published agents, newest first, a page at a time. Each row's
 * Review opens the same dialog a looked-up slug does — the list only says
 * which agents exist.
 */
function Catalogue({ onReview }: { onReview: (chosen: Chosen) => void }) {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");

  const catalogue = useInfiniteQuery({
    queryKey: ["hub-catalogue", query],
    queryFn: ({ pageParam }) => api.browseHub(query, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) =>
      last.listings.length >= HUB_CATALOGUE_PAGE_SIZE ? last.page + 1 : undefined,
  });
  const entries = catalogue.data?.pages.flatMap((page) => page.listings) ?? [];
  const trust = catalogue.data?.pages[0]?.trust ?? "";

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft.trim());
        }}
        className="flex items-center gap-2"
      >
        <input
          aria-label="Search Botcamp agents"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Search published agents"
          className="w-64 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        />
        <button
          type="submit"
          className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <Search className="h-3.5 w-3.5" />
          Search
        </button>
      </form>

      {catalogue.isPending && (
        <p className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading Botcamp's agents…
        </p>
      )}

      {catalogue.isError && (
        <p role="alert" data-hub-catalogue-error className="text-sm text-[var(--color-red)]">
          {(catalogue.error as Error).message}{" "}
          <button
            onClick={() => catalogue.refetch()}
            className="underline hover:text-[var(--color-text)]"
          >
            Try again
          </button>
        </p>
      )}

      {catalogue.isSuccess && entries.length === 0 && (
        <p data-hub-catalogue-empty className="text-sm text-[var(--color-text-muted)]">
          {query ? `No published agent matches "${query}".` : "Nothing is published on Botcamp yet."}
        </p>
      )}

      {entries.length > 0 && (
        <ul className="divide-y divide-[var(--color-border)] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
          {entries.map((entry) => (
            <CatalogueRow
              key={entry.slug}
              entry={entry}
              onReview={() => onReview({ slug: entry.slug, name: entry.name, trust })}
            />
          ))}
        </ul>
      )}

      {catalogue.hasNextPage && (
        <button
          onClick={() => catalogue.fetchNextPage()}
          disabled={catalogue.isFetchingNextPage}
          className="text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          {catalogue.isFetchingNextPage ? "Loading…" : "Show more"}
        </button>
      )}
    </div>
  );
}

function CatalogueRow({ entry, onReview }: { entry: HubCatalogueEntry; onReview: () => void }) {
  return (
    <li data-hub-catalogue-entry={entry.slug} className="flex items-start justify-between gap-3 px-3 py-2.5">
      <span className="min-w-0">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-sm font-semibold text-[var(--color-text)]">
            {entry.name || entry.slug}
          </span>
          {entry.badge === "validated" && (
            <span
              title="Approved by Botcamp's review"
              className="flex shrink-0 items-center gap-0.5 text-[10px] font-medium text-[var(--color-primary)]"
            >
              <BadgeCheck className="h-3 w-3" /> Validated
            </span>
          )}
          {!entry.free && (
            <span
              title="Needs a Botcamp membership or unlock to pull"
              className="flex shrink-0 items-center gap-0.5 text-[10px] font-medium text-[var(--color-text-muted)]"
            >
              <Lock className="h-3 w-3" /> Members
            </span>
          )}
        </span>
        <span className="block truncate text-xs text-[var(--color-text-muted)]">
          {entry.description || "No description."}
        </span>
        <span className="block text-[11px] text-[var(--color-text-muted)]">
          {entry.author.name ? `${entry.author.name} · ` : ""}
          <span className="font-mono">{entry.slug}</span> · version {entry.version}
        </span>
      </span>
      <button
        onClick={onReview}
        className="shrink-0 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
      >
        Review
      </button>
    </li>
  );
}
