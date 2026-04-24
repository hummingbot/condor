import { useMemo } from "react";

import { PriceTicker } from "@/components/market/PriceTicker";
import type { ExecutorInfo } from "@/lib/api";

interface AgentMarketStripProps {
  serverName: string;
  executors: ExecutorInfo[];
}

export function AgentMarketStrip({ serverName, executors }: AgentMarketStripProps) {
  // Extract unique connector:pair combinations
  const pairs = useMemo(() => {
    const seen = new Set<string>();
    const result: { connector: string; pair: string }[] = [];
    for (const ex of executors) {
      if (!ex.trading_pair || !ex.connector) continue;
      const key = `${ex.connector}:${ex.trading_pair}`;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push({ connector: ex.connector, pair: ex.trading_pair });
    }
    return result;
  }, [executors]);

  if (!serverName || pairs.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
      {pairs.map(({ connector, pair }) => (
        <div key={`${connector}:${pair}`} className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="rounded-md bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-[10px] font-bold uppercase text-[var(--color-primary)]">
              {pair}
            </span>
            <span className="rounded-md bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)]">
              {connector}
            </span>
          </div>
          <PriceTicker server={serverName} connector={connector} pair={pair} />
        </div>
      ))}
    </div>
  );
}
