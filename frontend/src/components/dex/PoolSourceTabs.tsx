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
 * Pills rather than underlined tabs, because there are seven of them and they no
 * longer sit on one baseline: a wrapped underline reads as two broken rules across
 * the header, while a pill is legible on whatever row it lands on.
 */
const TAB_BASE =
  "rounded-md px-2.5 py-1 text-xs font-medium transition-colors whitespace-nowrap";

function tabClass(active: boolean) {
  return `${TAB_BASE} ${
    active
      ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
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

      <AnchoredMenu anchor={anchor} open={open} onClose={close} className="w-44">
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
                  ? "bg-[var(--color-primary)]"
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
        className="w-56"
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
                      ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white"
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
  const searchLabel =
    source.kind === "gateway"
      ? "Filter pools by name…"
      : "Paste a pool or token address…";
  const searchShown = source.kind === "gateway" || isSearch;
  const showGatewayTabs = network.startsWith(GATEWAY_TAB_CHAIN);

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-border)] px-4 py-2">
      {/* Wraps rather than scrolls. Seven short labels fit one row on any desktop
          width, and on a phone a second row is reachable where a horizontal
          scroller inside a vertically-scrolling page is largely not. */}
      <ChainSelect
        chains={chains}
        network={network}
        onChange={onNetworkChange}
      />

      <div className="flex flex-wrap items-center gap-0.5">
        {GECKO_TABS.map((t) => (
          <button
            key={t.view}
            onClick={() => onSourceChange({ kind: "gecko", view: t.view })}
            className={tabClass(
              sameSource(source, { kind: "gecko", view: t.view }),
            )}
          >
            {t.label}
          </button>
        ))}
        {showGatewayTabs && (
          <>
            <span className="mx-1.5 h-4 w-px bg-[var(--color-border)]" />
            {GATEWAY_TABS.map((t) => (
              <button
                key={t.connector}
                onClick={() =>
                  onSourceChange({ kind: "gateway", connector: t.connector })
                }
                className={tabClass(
                  sameSource(source, { kind: "gateway", connector: t.connector }),
                )}
              >
                {t.label}
              </button>
            ))}
          </>
        )}
        <span className="mx-1.5 h-4 w-px bg-[var(--color-border)]" />
        <button
          onClick={() => onSourceChange({ kind: "favorites" })}
          className={`${tabClass(source.kind === "favorites")} flex items-center gap-1`}
          title="Pools you starred"
        >
          <Star
            className={`h-3 w-3 ${source.kind === "favorites" ? "fill-[var(--color-primary)] text-[var(--color-primary)]" : ""}`}
          />
          Favorites
          {favoriteCount > 0 && (
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {favoriteCount}
            </span>
          )}
        </button>
      </div>

      <div className="ml-auto flex items-center gap-2">
        {source.kind === "gecko" && (
          <DexFilter
            venues={venues}
            selected={selectedDexes}
            onChange={onDexesChange}
          />
        )}
        {searchShown && (
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder={searchLabel}
              spellCheck={false}
              className="w-64 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] py-1 pl-7 pr-6 text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]"
            />
            {query && (
              <button
                onClick={() => onQueryChange("")}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
