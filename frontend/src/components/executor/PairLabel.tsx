import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

// A base58 mint address (Solana pubkey) — 32–44 chars, no 0/O/I/l. LP/DEX executors
// store `trading_pair` as `<base_mint>-SOL` because Gateway can't resolve memecoins
// by symbol, so the raw mint shows in the UI unless we resolve it.
function looksLikeMint(s: string): boolean {
  return /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(s);
}

function shortMint(s: string): string {
  return s.length > 12 ? `${s.slice(0, 4)}…${s.slice(-4)}` : s;
}

/**
 * Renders a trading pair, resolving a raw base-mint to its ticker via GeckoTerminal
 * (e.g. `DezXAZ…263-SOL` → `Bonk-SOL`). Non-mint pairs (normal CEX symbols) render
 * unchanged. Falls back to a shortened mint while loading or if resolution fails.
 */
export function PairLabel({
  tradingPair,
  connector,
  className,
}: {
  tradingPair: string;
  connector?: string;
  className?: string;
}) {
  const dash = tradingPair.lastIndexOf("-");
  const base = dash > 0 ? tradingPair.slice(0, dash) : tradingPair;
  const quote = dash > 0 ? tradingPair.slice(dash + 1) : "";
  const isMint = looksLikeMint(base);

  const { data } = useQuery({
    queryKey: ["token-symbol", base, connector],
    queryFn: () => api.getTokenSymbol(base, connector),
    enabled: isMint,
    staleTime: 24 * 60 * 60 * 1000,
    retry: 1,
  });

  if (!isMint) return <span className={className}>{tradingPair}</span>;

  const symbol = data?.symbol;
  const label = symbol ? `${symbol}-${quote}` : `${shortMint(base)}-${quote}`;
  return (
    <span className={className} title={tradingPair}>
      {label}
    </span>
  );
}
