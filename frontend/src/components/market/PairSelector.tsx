import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Search, X } from "lucide-react";

import { api } from "@/lib/api";

interface PairSelectorProps {
  server: string;
  connector: string;
  value: string;
  onChange: (pair: string) => void;
}

const MAX_VISIBLE = 50;

export function PairSelector({
  server,
  connector,
  value,
  onChange,
}: PairSelectorProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const { data: rulesData, isLoading } = useQuery({
    queryKey: ["trading-rules", server, connector],
    queryFn: () => api.getTradingRules(server, connector),
    enabled: !!server && !!connector,
    staleTime: 5 * 60 * 1000,
  });

  const pairs = useMemo(
    () => rulesData?.rules?.map((r) => r.trading_pair).sort() ?? [],
    [rulesData],
  );

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

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Focus input when opening
  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
      setSearch("");
      setActiveIndex(0);
    }
  }, [open]);

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
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="group flex items-center gap-1 px-4 py-2.5 transition-colors hover:bg-[var(--color-surface-hover)] focus:outline-none"
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

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-72 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] shadow-xl shadow-black/40">
          {/* Search input */}
          <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
            <Search className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
            <input
              ref={inputRef}
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
              <button onClick={() => setSearch("")}>
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
                return (
                  <button
                    key={p}
                    data-pair-item
                    onClick={() => {
                      onChange(p);
                      setOpen(false);
                    }}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
                      i === activeIndex
                        ? "bg-[var(--color-primary)]/10 text-[var(--color-text)]"
                        : p === value
                          ? "text-[var(--color-primary)]"
                          : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                    }`}
                  >
                    <span className="font-medium">{base}</span>
                    <span className="text-[var(--color-text-muted)]">-{quote}</span>
                  </button>
                );
              })
            )}
            {filtered.length === MAX_VISIBLE && (
              <p className="px-3 py-1.5 text-center text-xs text-[var(--color-text-muted)]">
                Type to search more...
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Export the rules map hook for TradingRulesInfo
export function useTradingRules(server: string, connector: string) {
  const { data } = useQuery({
    queryKey: ["trading-rules", server, connector],
    queryFn: () => api.getTradingRules(server, connector),
    enabled: !!server && !!connector,
    staleTime: 5 * 60 * 1000,
  });
  return data;
}
