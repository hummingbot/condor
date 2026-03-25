import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, type OrderBookLevel } from "@/lib/api";

interface OrderBookProps {
  server: string;
  connector: string;
  pair: string;
}

const LEVELS = 15;

function formatNum(n: number, decimals: number) {
  if (n === 0) return "0";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 10_000) return (n / 1_000).toFixed(1) + "K";
  return n.toFixed(decimals);
}

function priceDecimals(levels: OrderBookLevel[]): number {
  if (!levels.length) return 2;
  const sample = levels[0].price;
  if (sample >= 1000) return 2;
  if (sample >= 1) return 4;
  if (sample >= 0.01) return 6;
  return 8;
}

export function OrderBook({ server, connector, pair }: OrderBookProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["order-book", server, connector, pair],
    queryFn: () => api.getOrderBook(server, connector, pair),
    enabled: !!server && !!connector && !!pair,
    refetchInterval: 2500,
  });

  const { asks, bids, spread, spreadPct, maxCumulative, pDecimals } =
    useMemo(() => {
      if (!data)
        return {
          asks: [] as (OrderBookLevel & { total: number })[],
          bids: [] as (OrderBookLevel & { total: number })[],
          spread: 0,
          spreadPct: 0,
          maxCumulative: 1,
          pDecimals: 2,
        };

      const pd = priceDecimals([
        ...(data.asks ?? []),
        ...(data.bids ?? []),
      ]);

      // Asks: lowest first, take LEVELS, then reverse for display (highest on top)
      const rawAsks = (data.asks ?? []).slice(0, LEVELS);
      let cum = 0;
      const asksWithTotal = rawAsks.map((l) => {
        cum += l.amount;
        return { ...l, total: cum };
      });
      // Reverse so highest ask is at top, lowest near spread
      asksWithTotal.reverse();

      // Bids: highest first (already sorted), take LEVELS
      const rawBids = (data.bids ?? []).slice(0, LEVELS);
      cum = 0;
      const bidsWithTotal = rawBids.map((l) => {
        cum += l.amount;
        return { ...l, total: cum };
      });

      const bestAsk = rawAsks.length ? rawAsks[0].price : 0;
      const bestBid = rawBids.length ? rawBids[0].price : 0;
      const sp = bestAsk - bestBid;
      const mid = (bestAsk + bestBid) / 2;
      const spPct = mid > 0 ? (sp / mid) * 100 : 0;

      const maxCum = Math.max(
        asksWithTotal[0]?.total ?? 0,
        bidsWithTotal[bidsWithTotal.length - 1]?.total ?? 0,
        1,
      );

      return {
        asks: asksWithTotal,
        bids: bidsWithTotal,
        spread: sp,
        spreadPct: spPct,
        maxCumulative: maxCum,
        pDecimals: pd,
      };
    }, [data]);

  if (!pair) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-[var(--color-text-muted)]">
        Select a pair
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-[var(--color-border)] px-3 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
          Order Book
        </h3>
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-3 gap-1 border-b border-[var(--color-border)] px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        <span>Price</span>
        <span className="text-right">Amount</span>
        <span className="text-right">Total</span>
      </div>

      {isLoading ? (
        <div className="flex flex-1 items-center justify-center text-xs text-[var(--color-text-muted)]">
          Loading...
        </div>
      ) : (
        <div className="mt-auto flex flex-col overflow-hidden">
          {/* Asks (reversed: highest at top) */}
          <div className="flex flex-col justify-end overflow-hidden">
            {asks.map((level, i) => (
              <Row
                key={`a-${i}`}
                price={level.price}
                amount={level.amount}
                total={level.total}
                maxCumulative={maxCumulative}
                side="ask"
                pDecimals={pDecimals}
              />
            ))}
          </div>

          {/* Spread */}
          <div className="flex items-center justify-between border-y border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
            <span className="text-xs font-medium text-[var(--color-text)]">
              {spread.toFixed(pDecimals)}
            </span>
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {spreadPct.toFixed(3)}% spread
            </span>
          </div>

          {/* Bids */}
          <div className="flex flex-col overflow-hidden">
            {bids.map((level, i) => (
              <Row
                key={`b-${i}`}
                price={level.price}
                amount={level.amount}
                total={level.total}
                maxCumulative={maxCumulative}
                side="bid"
                pDecimals={pDecimals}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({
  price,
  amount,
  total,
  maxCumulative,
  side,
  pDecimals,
}: {
  price: number;
  amount: number;
  total: number;
  maxCumulative: number;
  side: "bid" | "ask";
  pDecimals: number;
}) {
  const depthPct = Math.min((total / maxCumulative) * 100, 100);
  const color = side === "bid" ? "var(--color-green)" : "var(--color-red)";

  return (
    <div
      className="relative grid grid-cols-3 gap-1 px-3 py-[3px] text-[11px] font-mono tabular-nums hover:bg-[var(--color-surface-hover)]"
    >
      {/* Depth bar */}
      <div
        className="pointer-events-none absolute inset-y-0 right-0"
        style={{
          width: `${depthPct}%`,
          backgroundColor: color,
          opacity: 0.08,
        }}
      />
      <span style={{ color }} className="relative z-10">
        {price.toFixed(pDecimals)}
      </span>
      <span className="relative z-10 text-right text-[var(--color-text)]">
        {formatNum(amount, 4)}
      </span>
      <span className="relative z-10 text-right text-[var(--color-text-muted)]">
        {formatNum(total, 2)}
      </span>
    </div>
  );
}
