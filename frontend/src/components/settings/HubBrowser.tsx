import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import {
  BadgeCheck,
  Bot,
  ExternalLink,
  Loader2,
  Lock,
  Search,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import {
  api,
  errorStatus,
  HUB_CATALOGUE_PAGE_SIZE,
  type AgentImportPlan,
  type AgentUpdatePlan,
  type HubCatalogueEntry,
} from "@/lib/api";

import { useEscapeKey } from "@/hooks/useEscapeKey";

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
 * bytes; what makes that safe is the integrity check on the server — but
 * only once install is pinned to what the preview actually showed (BUG-2):
 * `version` here is usually `"latest"`, and asking Botcamp for "latest"
 * twice can resolve to two different approved versions if one landed in
 * between. So every preview call records the exact version and digest it
 * got back, and every install/update call that follows sends those back
 * instead of re-resolving `"latest"` — the server refuses rather than
 * silently applying whatever is newest by the time install runs.
 */
function hubSource(slug: string, version: string): BundleSource {
  let pinned = { version, sha256: "" };
  return {
    key: `hub:${slug}:${version}`,
    preview: async (asSlug) => {
      const res = await api.previewHubInstall(slug, asSlug, version);
      pinned = { version: String(res.fetched.version), sha256: res.fetched.sha256 };
      return res.verb === "update"
        ? { verb: "update", plan: res.plan as AgentUpdatePlan }
        : { verb: "install", plan: res.plan as AgentImportPlan };
    },
    install: async (asSlug) =>
      (await api.installFromHub(slug, asSlug, pinned.version, pinned.sha256))
        .plan as AgentImportPlan,
    previewUpdate: async () => {
      const res = await api.previewHubInstall(slug, "", version);
      pinned = { version: String(res.fetched.version), sha256: res.fetched.sha256 };
      return res.plan as AgentUpdatePlan;
    },
    applyUpdate: async () =>
      (await api.installFromHub(slug, "", pinned.version, pinned.sha256))
        .plan as AgentUpdatePlan,
  };
}

/** What the review dialog is opened on: one agent, by the slug a pull addresses. */
interface Chosen {
  slug: string;
  name: string;
  trust: string;
}

/**
 * One agent as a card, whichever way it was found.
 *
 * The catalogue and the by-slug lookup answer with different payloads —
 * Botcamp's catalogue carries the author, the badge and the paywall bit, a
 * single listing carries its version history — but they are the same object to
 * the operator, and the first cut drew them as two different things: a card in
 * the grid, a flat row under the lookup form. One shape, with the fields that
 * only one source has made optional.
 */
interface CardAgent {
  slug: string;
  name: string;
  description: string;
  /** The line under the description — the author and version, or the latest. */
  meta: string;
  validated: boolean;
  members: boolean;
  pageUrl: string;
}

/**
 * Pull an agent from Botcamp, and install it, without leaving Condor.
 *
 * Mounted on `/hub`'s Pull agents tab (FEAT-153) once `AgentHub` has
 * confirmed this install is linked to Botcamp — Botcamp never serves agent
 * code without a token, so there is nothing to pull before sign-in. Botcamp's
 * catalogue lists what is published, newest first; a private agent someone
 * shared is not in it, so the lookup by slug stays underneath — folded away
 * until it is asked for, because an always-open second search field beside the
 * catalogue's own was the one control everybody typed into by mistake. Either
 * way it answers the same question Import does — how does an agent get here —
 * and shares its review screen deliberately: a Botcamp agent is not a safer
 * kind of bundle, it is the same kind with a provenance label attached, and a
 * second review screen would imply otherwise.
 */
export function HubBrowser({ onInstalled }: { onInstalled: () => void }) {
  const [slug, setSlug] = useState("");
  const [looked, setLooked] = useState("");
  const [bySlug, setBySlug] = useState(false);
  const [chosen, setChosen] = useState<Chosen | null>(null);

  const lookup = useMutation({
    mutationFn: (value: string) => api.getHubListing(value),
  });

  return (
    <div className="space-y-5">
      <Catalogue onReview={setChosen} />

      <div className="border-t border-[var(--color-border)] pt-4">
        {bySlug ? (
          <div
            data-hub-by-slug
            className="space-y-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="text-xs font-bold text-[var(--color-text)]">
                  Look up by slug
                </h3>
                <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
                  Private agents aren't listed in the catalogue. Got the slug of
                  one somebody shared? Ask Botcamp for it directly.
                </p>
              </div>
              <button
                onClick={() => {
                  setBySlug(false);
                  lookup.reset();
                }}
                aria-label="Close slug lookup"
                className="shrink-0 rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

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

            {/* The server already tells apart "not found", "needs a membership
                or purchase" and "sign in" — this renders that sentence rather
                than a generic failure. */}
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
              <ul className="grid grid-cols-1 gap-3">
                <AgentCard
                  marker={{ "data-hub-listing": lookup.data.listing.slug }}
                  agent={{
                    slug: lookup.data.listing.slug,
                    name: lookup.data.listing.name,
                    description: lookup.data.listing.description,
                    meta: `latest version ${lookup.data.listing.latest}`,
                    validated: false,
                    members: false,
                    pageUrl: "",
                  }}
                  onReview={() =>
                    setChosen({
                      slug: lookup.data.listing.slug,
                      name: lookup.data.listing.name,
                      trust: lookup.data.trust,
                    })
                  }
                />
              </ul>
            )}
          </div>
        ) : (
          <button
            onClick={() => setBySlug(true)}
            className="flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            <Search className="h-3.5 w-3.5" />
            Look up a private agent by slug
          </button>
        )}
      </div>

      {/* The same dialog the file path uses, with the agent's provenance
          above it. Keyed on the slug so choosing a second one re-previews
          from scratch rather than showing the first one's plan. */}
      {/* The review opens over the page rather than below it: from a card
          high up in the grid, a dialog rendered under the slug lookup was
          off-screen, and pressing Review looked like it did nothing. */}
      {chosen && (
        <ReviewModal chosen={chosen} onClose={() => setChosen(null)}>
          <ImportAgentDialog
            key={chosen.slug}
            source={hubSource(chosen.slug, "latest")}
            onImported={onInstalled}
            stickyAction
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
        </ReviewModal>
      )}
    </div>
  );
}

/**
 * Botcamp's published agents, newest first, a page at a time. Each card's
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
      last.listings.length >= HUB_CATALOGUE_PAGE_SIZE
        ? last.page + 1
        : undefined,
  });
  const entries = catalogue.data?.pages.flatMap((page) => page.listings) ?? [];
  const trust = catalogue.data?.pages[0]?.trust ?? "";
  const botcampUrl = catalogue.data?.pages[0]?.botcamp_url ?? "";

  return (
    <div className="space-y-3">
      {/* One search field, full width, with the off-site link at the far end:
          the catalogue's own control is the only thing that looks like a
          search box on this tab. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(draft.trim());
          }}
          className="flex min-w-0 flex-1 items-center gap-2"
        >
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              aria-label="Search Botcamp agents"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Search published agents"
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] py-2 pl-8 pr-3 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
            />
          </div>
          <button
            type="submit"
            className="shrink-0 rounded-md border border-[var(--color-border)] px-3 py-2 text-xs font-semibold text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-text)]"
          >
            Search
          </button>
        </form>
        {botcampUrl && (
          <a
            href={botcampUrl}
            target="_blank"
            rel="noopener noreferrer"
            data-hub-botcamp-link
            className="flex shrink-0 items-center gap-1 text-xs font-semibold text-[var(--color-primary)] hover:underline"
          >
            Browse all on Botcamp <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      {/* What is on screen, said once: the active search and how many agents
          answered it. Without it, a filtered grid and the whole catalogue
          look identical. */}
      {query && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <span>
            Matching <span className="text-[var(--color-text)]">"{query}"</span>
          </span>
          <button
            onClick={() => {
              setDraft("");
              setQuery("");
            }}
            className="flex items-center gap-1 rounded border border-[var(--color-border)] px-1.5 py-0.5 hover:text-[var(--color-text)]"
          >
            <X className="h-3 w-3" /> Clear
          </button>
        </div>
      )}

      {catalogue.isPending && (
        <div className="@container" data-hub-catalogue-loading>
          <ul className="grid grid-cols-1 gap-3 @lg:grid-cols-2 @3xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <li
                key={i}
                className="h-36 animate-pulse rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]"
              />
            ))}
          </ul>
        </div>
      )}

      {catalogue.isError && (
        <p
          role="alert"
          data-hub-catalogue-error
          className="text-sm text-[var(--color-red)]"
        >
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
        <p
          data-hub-catalogue-empty
          className="rounded-xl border border-dashed border-[var(--color-border)] px-4 py-8 text-center text-sm text-[var(--color-text-muted)]"
        >
          {query
            ? `No published agent matches "${query}".`
            : "Nothing is published on Botcamp yet."}
        </p>
      )}

      {entries.length > 0 && (
        // Columns follow the Hub column's own width, not the window's: the
        // workspace pane can take half of it.
        <div className="@container">
          <ul className="grid grid-cols-1 gap-3 @lg:grid-cols-2 @3xl:grid-cols-3">
            {entries.map((entry) => (
              <AgentCard
                key={entry.slug}
                marker={{ "data-hub-catalogue-entry": entry.slug }}
                agent={catalogueAgent(entry)}
                onReview={() =>
                  onReview({ slug: entry.slug, name: entry.name, trust })
                }
              />
            ))}
          </ul>
        </div>
      )}

      {catalogue.hasNextPage && (
        <div className="flex justify-center pt-1">
          <button
            onClick={() => catalogue.fetchNextPage()}
            disabled={catalogue.isFetchingNextPage}
            className="rounded-md border border-[var(--color-border)] px-4 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-text)] disabled:opacity-50"
          >
            {catalogue.isFetchingNextPage ? "Loading…" : "Show more"}
          </button>
        </div>
      )}
    </div>
  );
}

function catalogueAgent(entry: HubCatalogueEntry): CardAgent {
  return {
    slug: entry.slug,
    name: entry.name,
    description: entry.description,
    meta: `${entry.author.name || entry.author.handle || "Unknown author"} · v${entry.version}`,
    validated: entry.badge === "validated",
    members: !entry.free,
    pageUrl: entry.page_url,
  };
}

function AgentCard({
  agent,
  marker,
  onReview,
}: {
  agent: CardAgent;
  /** The one `data-` hook callers address this card by. */
  marker: Record<string, string>;
  onReview: () => void;
}) {
  return (
    <li
      {...marker}
      className="flex h-full flex-col gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 transition-colors hover:border-[var(--color-primary)]/40"
    >
      <div className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-[var(--color-primary)]/10 text-[var(--color-primary)]">
          <Bot className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm font-semibold leading-snug text-[var(--color-text)]">
            {agent.name || agent.slug}
          </p>
          {/* The slug is what a pull addresses and what somebody would share,
              so it is on the card rather than only in a hover title. */}
          <p className="truncate font-mono text-[11px] text-[var(--color-text-muted)]">
            {agent.slug}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {agent.validated && (
            <span
              title="Approved by Botcamp's review"
              className="flex items-center gap-0.5 rounded border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-primary)]"
            >
              <BadgeCheck className="h-3 w-3" /> Validated
            </span>
          )}
          {agent.members && (
            <span
              title="Needs a Botcamp membership or unlock to pull"
              className="flex items-center gap-0.5 rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)]"
            >
              <Lock className="h-3 w-3" /> Members
            </span>
          )}
        </div>
      </div>

      <p className="line-clamp-3 flex-1 text-xs leading-relaxed text-[var(--color-text-muted)]">
        {agent.description || "No description."}
      </p>

      <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border)] pt-2.5">
        <span className="min-w-0 truncate text-[11px] text-[var(--color-text-muted)]">
          {agent.meta}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {agent.pageUrl && (
            <a
              href={agent.pageUrl}
              target="_blank"
              rel="noopener noreferrer"
              title="Open this agent's page on Botcamp"
              aria-label={`View ${agent.slug} on Botcamp`}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          <button
            onClick={onReview}
            className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
          >
            Review
          </button>
        </span>
      </div>
    </li>
  );
}

function ReviewModal({
  chosen,
  onClose,
  children,
}: {
  chosen: Chosen;
  onClose: () => void;
  children: ReactNode;
}) {
  useEscapeKey(true, onClose);
  return (
    <div
      data-hub-review
      role="dialog"
      aria-modal="true"
      aria-label={`Review ${chosen.name || chosen.slug}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] px-5 py-3">
          <h2 className="min-w-0 truncate text-sm font-semibold text-[var(--color-text)]">
            Review before installing
          </h2>
          <button
            onClick={onClose}
            aria-label="Close review"
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {/* No bottom padding: the review's Import bar sticks to this edge, and
            padding under it would let the scrolled text show beneath. */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pt-5">
          {children}
        </div>
      </div>
    </div>
  );
}
