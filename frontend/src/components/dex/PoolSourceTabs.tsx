import { Check, ChevronDown, Search, Star, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import type { DexChain, DexVenue } from "@/lib/api";

import {
  GATEWAY_TAB_CHAIN,
  GATEWAY_TABS,
  GECKO_TABS,
  type PoolSource,
  sameSource,
} from "./pool-source";


/**
 * Pills rather than underlined tabs, because there are several groups of them
 * and they do not always sit on one baseline: a wrapped underline reads as two broken rules across
 * the header, while a pill is legible on whatever row it lands on.
 */
const TAB_BASE =
  "rounded-md px-2.5 py-1 text-xs font-medium transition-colors whitespace-nowrap";

/** The small caption heading each group of pills. */
const GROUP_LABEL =
  "mr-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]";

function tabClass(active: boolean) {
  return `${TAB_BASE} ${
    active
      ? "bg-[var(--color-primary-fill)]/15 text-[var(--color-primary)]"
      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
  }`;
}

/**
 * The chain the browser is pointed at.
 *
 * Single-select, unlike the venue filter: a pool lives on exactly one chain, and
 * every row's address, chart and executor is scoped to it, so "Solana and Base at
 * once" is not a view the workspace behind a row could honour.
 */
function ChainSelect({
  chains,
  network,
  onChange,
}: {
  chains: DexChain[];
  network: string;
  onChange: (next: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  // One chain is not a choice — render it as a label rather than a dead dropdown.
  if (chains.length <= 1) return null;

  const current = chains.find((c) => c.network_id === network);

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title="Chain to browse pools on"
        className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        {current?.label ?? network}
        <ChevronDown
          className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      <AnchoredMenu anchor={anchor} open={open} onClose={close} className="w-44 py-1">
        {chains.map((c) => (
          <button
            key={c.network_id}
            onClick={() => {
              onChange(c.network_id);
              setOpen(false);
            }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-surface-hover)]"
          >
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                c.network_id === network
                  ? "bg-[var(--color-primary-fill)]"
                  : "bg-transparent"
              }`}
            />
            <span className="truncate">{c.label}</span>
          </button>
        ))}
      </AnchoredMenu>
    </>
  );
}

/**
 * The venue filter: "top pools, but only on these DEXes".
 *
 * Multi-select rather than a single choice because the question it answers is
 * "which of these venues do I trade", which is rarely one — and its options come
 * from the chain, so a venue GeckoTerminal starts indexing appears on its own.
 */
function DexFilter({
  venues,
  selected,
  onChange,
}: {
  venues: DexVenue[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const matches = needle
      ? venues.filter(
          (v) =>
            v.name.toLowerCase().includes(needle) ||
            v.id.toLowerCase().includes(needle),
        )
      : venues;
    // Selected venues stay visible even when the search would hide them —
    // otherwise unticking the last match means retyping to find it again.
    const picked = venues.filter(
      (v) => selected.includes(v.id) && !matches.includes(v),
    );
    return [...picked, ...matches];
  }, [venues, filter, selected]);

  if (!venues.length) return null;

  const label =
    selected.length === 0
      ? "All DEXes"
      : selected.length === 1
        ? (venues.find((v) => v.id === selected[0])?.name ?? selected[0])
        : `${selected.length} DEXes`;

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors ${
          selected.length
            ? "border-[var(--color-primary)] text-[var(--color-primary)]"
            : "border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
        }`}
      >
        {label}
        {selected.length > 0 ? (
          <X
            className="h-3 w-3"
            onClick={(e) => {
              e.stopPropagation();
              onChange([]);
            }}
          />
        ) : (
          <ChevronDown
            className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`}
          />
        )}
      </button>

      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={close}
        align="right"
        className="w-56 py-1"
      >
        <div className="px-2 pb-1">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter venues…"
            autoFocus
            spellCheck={false}
            className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]"
          />
        </div>
        <div>
          {shown.map((v) => {
              const active = selected.includes(v.id);
            return (
              <button
                key={v.id}
                onClick={() =>
                  onChange(
                    active
                      ? selected.filter((id) => id !== v.id)
                      : [...selected, v.id],
                  )
                }
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-surface-hover)]"
              >
                <span
                  className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border ${
                    active
                      ? "border-[var(--color-primary)] bg-[var(--color-primary-fill)] text-[var(--on-primary)]"
                      : "border-[var(--color-border)]"
                  }`}
                >
                  {active && <Check className="h-2.5 w-2.5" />}
                </span>
                <span className="truncate">{v.name}</span>
              </button>
            );
          })}
          {!shown.length && (
            <div className="px-3 py-2 text-xs text-[var(--color-text-muted)]">
              No venue matches.
            </div>
          )}
        </div>
      </AnchoredMenu>
    </>
  );
}

interface Props {
  source: PoolSource;
  onSourceChange: (s: PoolSource) => void;
  /** The chains this Gateway can browse. One or none hides the selector. */
  chains: DexChain[];
  network: string;
  onNetworkChange: (next: string) => void;
  /** The venues on this chain, for the filter. Empty hides it. */
  venues: DexVenue[];
  selectedDexes: string[];
  onDexesChange: (next: string[]) => void;
  query: string;
  onQueryChange: (q: string) => void;
  favoriteCount: number;
}

export function PoolSourceTabs({
  source,
  onSourceChange,
  chains,
  network,
  onNetworkChange,
  venues,
  selectedDexes,
  onDexesChange,
  query,
  onQueryChange,
  favoriteCount,
}: Props) {
  const isSearch = source.kind === "gecko" && source.view === "token";
  const showGatewayTabs = network.startsWith(GATEWAY_TAB_CHAIN);
  /**
   * Where a search goes back to when it is cleared.
   *
   * Search used to be a fourth tab beside Trending/Top/New — an action dressed
   * as a list. The field is always on screen now: pasting an address into it is
   * the search, and emptying it returns to the list the reader came from rather
   * than leaving them on an empty results table.
   */
  const [lastList, setLastList] = useState<PoolSource>(
    isSearch ? { kind: "gecko", view: "trending" } : source,
  );
  // A list remembered on one chain means nothing on another: the parent
  // already resets to Trending when the chain changes, so this does too —
  // otherwise clearing a search on Base brought back Solana's Meteora list.
  // Adjusted during render (React's "reset state on prop change" pattern),
  // not in an effect, so there is no frame with the stale list.
  const [listNetwork, setListNetwork] = useState(network);
  if (listNetwork !== network) {
    setListNetwork(network);
    setLastList({ kind: "gecko", view: "trending" });
  }
  const pick = (next: PoolSource) => {
    setLastList(next);
    onQueryChange("");
    onSourceChange(next);
  };
  const onSearchInput = (value: string) => {
    onQueryChange(value);
    // On a Gateway list the field filters that list by name, in place.
    if (source.kind === "gateway") return;
    if (value.trim() && !isSearch) onSourceChange({ kind: "gecko", view: "token" });
    if (!value.trim() && isSearch) onSourceChange(lastList);
  };
  const searchLabel =
    source.kind === "gateway" ? "Filter pools by name…" : "Paste a pool or token address…";

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-border)] px-4 py-2">
      {/* Wraps rather than scrolls: on a phone a second row is reachable where a
          horizontal scroller inside a vertically-scrolling page is largely not. */}
      <ChainSelect chains={chains} network={network} onChange={onNetworkChange} />

      {/* Your own shortlist first and on its own: it is not a ranking like
          Trending/Top/New, so sitting among them made it read as one. */}
      <button
        onClick={() => pick({ kind: "favorites" })}
        className={`${tabClass(source.kind === "favorites")} flex items-center gap-1`}
        title="Pools you starred"
      >
        <Star
          className={`h-3 w-3 ${source.kind === "favorites" ? "fill-[var(--color-primary)] text-[var(--color-primary)]" : ""}`}
        />
        Favorites
        {favoriteCount > 0 && (
          <span className="text-[11px] text-[var(--color-text-muted)]">{favoriteCount}</span>
        )}
      </button>
      <span aria-hidden className="h-4 w-px bg-[var(--color-border)]" />

      {/* Lists to browse, and Gateway's own pools per venue, as two labelled
          groups — they used to run together in one strip with the search
          action and the favorites between them. */}
      <div className="flex flex-wrap items-center gap-0.5">
        <span className={GROUP_LABEL}>Browse</span>
        {GECKO_TABS.filter((t) => t.view !== "token").map((t) => (
          <button
            key={t.view}
            onClick={() => pick({ kind: "gecko", view: t.view })}
            className={tabClass(sameSource(source, { kind: "gecko", view: t.view }))}
          >
            {t.label}
          </button>
        ))}
      </div>

      {showGatewayTabs && (
        <div className="flex flex-wrap items-center gap-0.5">
          <span
            className={GROUP_LABEL}
            title="Pools Gateway lists for this venue — the ones it can open positions in"
          >
            On Gateway
          </span>
          {GATEWAY_TABS.map((t) => (
            <button
              key={t.connector}
              onClick={() => pick({ kind: "gateway", connector: t.connector })}
              className={tabClass(sameSource(source, { kind: "gateway", connector: t.connector }))}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      <div className="ml-auto flex items-center gap-2">
        {/* The venue filter narrows the browse lists; a Gateway list is one
            venue already, and a search result is one pool. */}
        {source.kind === "gecko" && !isSearch && (
          <DexFilter venues={venues} selected={selectedDexes} onChange={onDexesChange} />
        )}
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--color-text-muted)]" />
          <input
            value={query}
            onChange={(e) => onSearchInput(e.target.value)}
            placeholder={searchLabel}
            spellCheck={false}
            aria-label={searchLabel}
            className="w-64 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] py-1 pl-7 pr-6 text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]"
          />
          {query && (
            <button
              onClick={() => onSearchInput("")}
              aria-label="Clear search"
              className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
