import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Search, X } from "lucide-react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { api } from "@/lib/api";
import { formatCompactVolume } from "@/lib/formatters";
import { useTickers } from "./useTickers";

interface PairSelectorProps {
  server: string;
  connector: string;
  value: string;
  onChange: (pair: string) => void;
  /**
   * Whether `/market/trading-rules` answers for this connector. False for gateway
   * DEX networks, which have no pair list at all — the selector becomes free-text
   * entry backed by a recents list.
   */
  hasTradingRules?: boolean;
}

const MAX_VISIBLE = 50;

/** Recently-entered DEX pairs, per network. */
const DEX_PAIRS_KEY = "condor_dex_pairs";
const MAX_DEX_RECENTS = 12;

function loadDexRecents(connector: string): string[] {
  try {
    const raw = localStorage.getItem(`${DEX_PAIRS_KEY}:${connector}`);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((p) => typeof p === "string") : [];
  } catch {
    return [];
  }
}

// An EVM 0x-address or a base58 Solana pubkey. A DEX pair may carry a raw mint as
// its base (`<mint>-SOL`) — base58 is case-sensitive, so such a side must survive
// normalization untouched. Mirrors ADDRESS_RE in condor/dex_candles.py.
const ADDRESS_RE = /^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/;

/** `sol-usdc` → `SOL-USDC`, leaving an address side exactly as typed. */
function normalizeDexPair(input: string): string {
  const trimmed = input.trim();
  const dash = trimmed.lastIndexOf("-");
  if (dash <= 0) return ADDRESS_RE.test(trimmed) ? trimmed : trimmed.toUpperCase();
  const base = trimmed.slice(0, dash);
  const quote = trimmed.slice(dash + 1);
  const up = (side: string) =>
    ADDRESS_RE.test(side) ? side : side.toUpperCase();
  return `${up(base)}-${up(quote)}`;
}

function rememberDexPair(connector: string, pair: string) {
  try {
    const next = [pair, ...loadDexRecents(connector).filter((p) => p !== pair)].slice(
      0,
      MAX_DEX_RECENTS,
    );
    localStorage.setItem(`${DEX_PAIRS_KEY}:${connector}`, JSON.stringify(next));
  } catch {
    /* ok */
  }
}

export function PairSelector({
  server,
  connector,
  value,
  onChange,
  hasTradingRules = true,
}: PairSelectorProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);

  const { data: rulesData, isLoading } = useQuery({
    queryKey: ["trading-rules", server, connector],
    queryFn: () => api.getTradingRules(server, connector),
    enabled: !!server && !!connector && hasTradingRules,
    staleTime: 5 * 60 * 1000,
  });

  const { byPair, rankByPair, hasTickers } = useTickers(
    server,
    connector,
    hasTradingRules,
  );

  // Tradable pairs come from trading rules; tickers only decide the order and the
  // volume badge, so the selector still works on servers without /market-data/tickers.
  const pairs = useMemo(() => {
    const list = rulesData?.rules?.map((r) => r.trading_pair) ?? [];
    if (!hasTickers) return list.sort();
    const unranked = rankByPair.size;
    return list.sort((a, b) => {
      const ra = rankByPair.get(a) ?? unranked;
      const rb = rankByPair.get(b) ?? unranked;
      return ra !== rb ? ra - rb : a.localeCompare(b);
    });
  }, [rulesData, hasTickers, rankByPair]);

  // Group pairs by quote asset
  const quoteGroups = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const p of pairs) {
      const parts = p.split("-");
      const quote = parts.length > 1 ? parts[parts.length - 1] : "OTHER";
      if (!groups.has(quote)) groups.set(quote, []);
      groups.get(quote)!.push(p);
    }
    // Sort: USDT first, then USDC, BTC, ETH, rest alphabetically
    const priority = ["USDT", "USDC", "BTC", "ETH"];
    return [...groups.entries()].sort(([a], [b]) => {
      const ai = priority.indexOf(a);
      const bi = priority.indexOf(b);
      if (ai !== -1 && bi !== -1) return ai - bi;
      if (ai !== -1) return -1;
      if (bi !== -1) return 1;
      return a.localeCompare(b);
    });
  }, [pairs]);

  const filtered = useMemo(() => {
    if (!search) return pairs.slice(0, MAX_VISIBLE);
    const q = search.toUpperCase();
    return pairs.filter((p) => p.toUpperCase().includes(q)).slice(0, MAX_VISIBLE);
  }, [pairs, search]);

  // Scroll active item into view
  useEffect(() => {
    if (!open || !listRef.current) return;
    const items = listRef.current.querySelectorAll("[data-pair-item]");
    items[activeIndex]?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, open]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && filtered[activeIndex]) {
      e.preventDefault();
      onChange(filtered[activeIndex]);
      setOpen(false);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  // A gateway network has no pair list to offer: the user types the pair.
  if (!hasTradingRules) {
    return <DexPairEntry connector={connector} value={value} onChange={onChange} />;
  }

  // Fallback to plain text input if no rules available
  if (!isLoading && pairs.length === 0) {
    return (
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Trading pair (e.g. BTC-USDT)"
        className="w-44 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
      />
    );
  }

  return (
    <>
      <button
        ref={setAnchor}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => {
          // The panel only exists while open, so its search state is reset on
          // the way in rather than by an effect that watches `open`.
          setSearch("");
          setActiveIndex(0);
          setOpen(!open);
        }}
        className="group flex items-center gap-1 px-4 py-2.5 transition-colors hover:bg-[var(--color-surface-hover)] focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[var(--color-primary)]"
      >
        {isLoading ? (
          <span className="text-sm text-[var(--color-text-muted)]">Loading...</span>
        ) : value ? (
          <span className="text-[15px] font-semibold text-[var(--color-text)]">{value}</span>
        ) : (
          <span className="text-sm text-[var(--color-text-muted)]">Select pair</span>
        )}
        <ChevronDown className="ml-1 h-3.5 w-3.5 text-[var(--color-text-muted)] transition-transform group-hover:text-[var(--color-text)]" />
      </button>

      <AnchoredMenu anchor={anchor} open={open} onClose={close} className="w-72 shadow-black/40">
        <>
          {/* Search input */}
          <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
            <Search className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
            <input
              autoFocus
              type="text"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={handleKeyDown}
              placeholder="Search pairs..."
              className="flex-1 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
            />
            {search && (
              <button onClick={() => setSearch("")} title="Clear search" aria-label="Clear search">
                <X className="h-3.5 w-3.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]" />
              </button>
            )}
          </div>

          {/* Quote asset tabs (only when not searching) */}
          {!search && quoteGroups.length > 1 && (
            <div className="flex gap-1 overflow-x-auto border-b border-[var(--color-border)] px-2 py-1.5 scrollbar-none">
              {quoteGroups.slice(0, 6).map(([quote]) => (
                <button
                  key={quote}
                  onClick={() => setSearch(`-${quote}`)}
                  className="shrink-0 rounded px-2 py-0.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                >
                  {quote}
                </button>
              ))}
            </div>
          )}

          {/* Pair list */}
          <div ref={listRef} className="max-h-64 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <p className="px-3 py-4 text-center text-xs text-[var(--color-text-muted)]">
                No pairs found
              </p>
            ) : (
              filtered.map((p, i) => {
                const [base, quote] = p.split("-");
                const usdVolume = byPair.get(p)?.usd_volume;
                return (
                  <button
                    key={p}
                    data-pair-item
                    onClick={() => {
                      onChange(p);
                      setOpen(false);
                    }}
                    className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-sm ${
                      i === activeIndex
                        ? "bg-[var(--color-primary)]/10 text-[var(--color-text)]"
                        : p === value
                          ? "text-[var(--color-primary)]"
                          : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                    }`}
                  >
                    <span><span className="font-medium">{base}</span><span className="text-[var(--color-text-muted)]">-{quote}</span></span>
                    {usdVolume != null && (
                      <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-text-muted)]">
                        {formatCompactVolume(usdVolume)}
                      </span>
                    )}
                  </button>
                );
              })
            )}
            {filtered.length === MAX_VISIBLE && (
              <p className="px-3 py-1.5 text-center text-xs text-[var(--color-text-muted)]">
                {hasTickers && !search
                  ? `Top ${MAX_VISIBLE} by 24h volume — type to search more...`
                  : "Type to search more..."}
              </p>
            )}
          </div>
        </>
      </AnchoredMenu>
    </>
  );
}

/**
 * Pair entry for a gateway DEX network.
 *
 * There is no tradable-pair list to browse — Gateway resolves whatever the user
 * names — so this is free-text `BASE-QUOTE` entry, the same grammar the Telegram
 * DEX flow uses, with the pairs already tried on this network kept as shortcuts.
 * A raw mint address is a valid base (`<mint>-SOL`); `_resolve_pool` expects
 * exactly that shape and `PairLabel` renders it back as a ticker.
 */
function DexPairEntry({
  connector,
  value,
  onChange,
}: {
  connector: string;
  value: string;
  onChange: (pair: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [recents, setRecents] = useState<string[]>(() => loadDexRecents(connector));
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    setRecents(loadDexRecents(connector));
  }, [connector]);

  // The input only exists while the menu is open, so it is focused on mount
  // rather than by an effect. A stable callback keeps React from re-running it
  // — and re-selecting the text — on every keystroke.
  const focusAndSelect = useCallback((el: HTMLInputElement | null) => {
    el?.focus();
    el?.select();
  }, []);

  const commit = (raw: string) => {
    const pair = normalizeDexPair(raw);
    if (!pair) return;
    rememberDexPair(connector, pair);
    setRecents(loadDexRecents(connector));
    onChange(pair);
    setOpen(false);
  };

  return (
    <>
      <button
        ref={setAnchor}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => {
          setDraft(value);
          setOpen(!open);
        }}
        className="group flex items-center gap-1 px-4 py-2.5 transition-colors hover:bg-[var(--color-surface-hover)] focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[var(--color-primary)]"
      >
        {value ? (
          <span className="max-w-[13rem] truncate text-[15px] font-semibold text-[var(--color-text)]" title={value}>
            {value}
          </span>
        ) : (
          <span className="text-sm text-[var(--color-text-muted)]">Enter pair</span>
        )}
        <ChevronDown className="ml-1 h-3.5 w-3.5 text-[var(--color-text-muted)] transition-transform group-hover:text-[var(--color-text)]" />
      </button>

      <AnchoredMenu anchor={anchor} open={open} onClose={close} className="w-80 shadow-black/40">
        <>
          <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
            <input
              ref={focusAndSelect}
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commit(draft);
                } else if (e.key === "Escape") {
                  setOpen(false);
                }
              }}
              placeholder="SOL-USDC or <mint>-SOL"
              spellCheck={false}
              autoComplete="off"
              className="flex-1 bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
            />
            <button
              onClick={() => commit(draft)}
              disabled={!draft.trim()}
              className="shrink-0 rounded border border-[var(--color-border)] px-2 py-0.5 text-[10px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-40"
            >
              Set
            </button>
          </div>

          {recents.length > 0 && (
            <div className="max-h-64 overflow-y-auto py-1">
              <p className="px-3 pb-0.5 pt-1 text-[9px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                Recent
              </p>
              {recents.map((p) => (
                <button
                  key={p}
                  onClick={() => commit(p)}
                  className={`flex w-full items-center px-3 py-1.5 text-left text-sm ${
                    p === value
                      ? "text-[var(--color-primary)]"
                      : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  <span className="truncate" title={p}>{p}</span>
                </button>
              ))}
            </div>
          )}

          <p className="border-t border-[var(--color-border)] px-3 py-1.5 text-[10px] text-[var(--color-text-muted)]">
            Gateway resolves the pool from the pair — a token mint works as the base.
          </p>
        </>
      </AnchoredMenu>
    </>
  );
}

// Export the rules map hook for CreateExecutor: pair list and price precision
export function useTradingRules(server: string, connector: string, enabled = true) {
  const { data } = useQuery({
    queryKey: ["trading-rules", server, connector],
    queryFn: () => api.getTradingRules(server, connector),
    enabled: !!server && !!connector && enabled,
    staleTime: 5 * 60 * 1000,
  });
  return data;
}
