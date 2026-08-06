import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

// An EVM 0x-address or a base58 Solana pubkey. LP/DEX executors store their
// trading_pair as `<base_address>-<quote>` (Gateway can't resolve memecoins by
// symbol), so the raw address is what reaches the UI unless we resolve it.
// Mirrors _ADDRESS_RE in condor/web/routes/market.py.
const ADDRESS_RE = /^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$/;

function shortAddress(s: string): string {
  return s.length > 12 ? `${s.slice(0, 4)}…${s.slice(-4)}` : s;
}

/**
 * Renders a trading pair, resolving a raw base address to its ticker via
 * GeckoTerminal (`DezXAZ…263-SOL` → `Bonk-SOL`). Ordinary CEX pairs render
 * unchanged and issue no request. While loading, or if the token is unknown, it
 * falls back to a shortened address; the full pair is always in the tooltip.
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
  const isAddress = ADDRESS_RE.test(base);

  const { data } = useQuery({
    queryKey: ["token-symbol", base, connector],
    queryFn: () => api.getTokenSymbol(base, connector),
    enabled: isAddress,
    staleTime: 24 * 60 * 60 * 1000,
    retry: 1,
  });

  if (!isAddress) return <span className={className}>{tradingPair}</span>;

  const label = data?.symbol
    ? `${data.symbol}-${quote}`
    : `${shortAddress(base)}-${quote}`;
  return (
    <span className={className} title={tradingPair}>
      {label}
    </span>
  );
}
