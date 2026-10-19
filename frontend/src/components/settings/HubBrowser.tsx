import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Loader2, Search, Users } from "lucide-react";
import { useState } from "react";

import {
  api,
  type AgentImportPlan,
  type AgentUpdatePlan,
  type HubListing,
} from "@/lib/api";

import { type BundleSource } from "./bundleSource";
import { ImportAgentDialog } from "./ImportAgentDialog";

/**
 * A listing, as the four calls the review dialog makes (FEAT-146).
 *
 * The dialog is reused rather than reimplemented, and this is the whole of the
 * difference between a file on disk and a listing on the hub. The reason is
 * not economy: the review *is* the sandbox, so a second review screen would be
 * a second chance to omit the executables list, the findings or the
 * muted-on-arrival sentence — and the screen that got it wrong would be the
 * one the operator actually used.
 *
 * Both `install` and `applyUpdate` post to the same endpoint, because the
 * server decides which verb it is from the bundle's identity and not from
 * which button was pressed. Each re-fetches rather than reusing the preview's
 * bytes; what makes that safe is the digest check on the server, which holds
 * both fetches to the same listing digest.
 */
function hubSource(listingId: string): BundleSource {
  return {
    key: `hub:${listingId}`,
    preview: async (asSlug) => {
      const res = await api.previewHubInstall(listingId, asSlug);
      return res.verb === "update"
        ? { verb: "update", plan: res.plan as AgentUpdatePlan }
        : { verb: "install", plan: res.plan as AgentImportPlan };
    },
    install: async (asSlug) =>
      (await api.installFromHub(listingId, asSlug)).plan as AgentImportPlan,
    previewUpdate: async () =>
      (await api.previewHubInstall(listingId)).plan as AgentUpdatePlan,
    applyUpdate: async () =>
      (await api.installFromHub(listingId)).plan as AgentUpdatePlan,
  };
}

/**
 * What a badge is, in the two words beside it. The sentence is the server's.
 *
 * Rendered as provenance and never as a verdict: `validated` says a human
 * looked at one submitted revision once, which is a fact about the past and
 * about bytes that are not necessarily these bytes.
 */
function Badge({ badge }: { badge: string }) {
  const validated = badge === "validated";
  return (
    <span
      data-hub-badge={badge}
      title={
        validated
          ? "A human reviewed one submitted revision of this, once. Not a signature."
          : "Nobody reviewed this. It installs on exactly the same terms."
      }
      className={`flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
        validated
          ? "border-[var(--color-primary)]/30 bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
          : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)]"
      }`}
    >
      {validated ? (
        <BadgeCheck className="h-3 w-3" />
      ) : (
        <Users className="h-3 w-3" />
      )}
      {validated ? "Reviewed once" : "Community"}
    </span>
  );
}

/**
 * Browse the hub and install from it, without leaving Condor.
 *
 * It lives beside Import and Export in the Agents panel because it answers the
 * same question — how does an agent get here — and the three doors are
 * deliberately one screen: a hub listing is not a safer kind of bundle, it is
 * the same kind with a provenance label attached, and separating them would
 * imply otherwise.
 */
export function HubBrowser({ onInstalled }: { onInstalled: () => void }) {
  const [search, setSearch] = useState("");
  /** What the operator typed, once they pressed Search. */
  const [applied, setApplied] = useState("");
  const [chosen, setChosen] = useState<HubListing | null>(null);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["hub-agents", applied],
    queryFn: () => api.browseHub(applied),
    // The hub is somebody else's service over the network: a refetch on every
    // tab focus would be a request to it for nothing an operator asked for.
    staleTime: 60_000,
    retry: false,
  });

  return (
    <div className="space-y-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(search.trim());
          setChosen(null);
        }}
        className="flex items-center gap-2"
      >
        <input
          aria-label="Search the hub"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search agents…"
          className="w-64 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        />
        <button
          type="submit"
          disabled={isFetching}
          className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          <Search className="h-3.5 w-3.5" />
          Search
        </button>
      </form>

      {isLoading && (
        <p className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" /> Asking the hub…
        </p>
      )}

      {/* The hub is a service that can be down, and that is not Condor being
          broken — so it says which one it is, and offers the retry rather than
          leaving an empty list that reads as "nothing published yet". */}
      {error && (
        <p role="alert" data-hub-error className="text-sm text-[var(--color-red)]">
          {(error as Error).message}{" "}
          <button
            onClick={() => refetch()}
            className="underline hover:text-[var(--color-text)]"
          >
            Try again
          </button>
        </p>
      )}

      {data && data.listings.length === 0 && !error && (
        <p className="text-sm text-[var(--color-text-muted)]">
          The hub has nothing matching that.
        </p>
      )}

      {data && data.listings.length > 0 && (
        <ul className="space-y-1.5">
          {data.listings.map((listing) => (
            <li
              key={listing.listing_id}
              data-hub-listing={listing.listing_id}
              className="flex items-start justify-between gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2"
            >
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-[var(--color-text)]">
                    {listing.name || listing.slug}
                  </span>
                  <Badge badge={listing.badge} />
                </span>
                <span className="block truncate text-xs text-[var(--color-text-muted)]">
                  {listing.description || "No description."}
                </span>
                <span className="block text-[11px] text-[var(--color-text-muted)]">
                  <span className="font-mono">{listing.slug}</span> · version{" "}
                  {listing.version} · {listing.bytes} bytes
                  {listing.published_at && ` · published ${listing.published_at}`}
                </span>
                {/* Labelled, because a handle rendered beside a download reads
                    as an identity Condor checked, and Condor checks nothing
                    here: the hub says it authenticated this account, and
                    nothing binds the account to these bytes. */}
                {(listing.author.handle || listing.author.name) && (
                  <span
                    data-hub-author
                    className="block text-[11px] text-[var(--color-text-muted)]"
                  >
                    By{" "}
                    <span className="text-[var(--color-text)]">
                      {listing.author.handle || listing.author.name}
                    </span>{" "}
                    — an account the hub authenticated, not a signature over
                    this file.
                  </span>
                )}
              </span>
              <button
                onClick={() => setChosen(listing)}
                className="shrink-0 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              >
                Review
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* The same dialog the file path uses, with the listing's provenance
          above it. Keyed on the listing so choosing a second one re-previews
          from scratch rather than showing the first one's plan. */}
      {chosen && (
        <ImportAgentDialog
          key={chosen.listing_id}
          source={hubSource(chosen.listing_id)}
          onImported={onInstalled}
          provenance={
            <div
              data-hub-provenance
              className="space-y-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5"
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-[var(--color-text)]">
                  {chosen.name || chosen.slug}
                </span>
                <Badge badge={chosen.badge} />
              </div>
              <p className="text-[11px] text-[var(--color-text-muted)]">
                {data?.trust}
              </p>
              {chosen.findings.length > 0 && (
                <p
                  data-hub-findings
                  className="text-[11px] text-[var(--color-text-muted)]"
                >
                  The hub's own review of this submission found{" "}
                  {chosen.findings.length} thing
                  {chosen.findings.length !== 1 ? "s" : ""}
                  {chosen.review_version && ` (${chosen.review_version})`}. The
                  list below is this install's review of the file it actually
                  received; read that one.
                </p>
              )}
            </div>
          }
        />
      )}
    </div>
  );
}
