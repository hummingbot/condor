import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

interface PriceTickerProps {
  server: string;
  connector: string;
  pair: string;
}

export function PriceTicker({ server, connector, pair }: PriceTickerProps) {
  const prevPriceRef = useRef<number>(0);

  const { data: price } = useQuery({
    queryKey: ["price", server, connector, pair],
    queryFn: () => api.getPrice(server, connector, pair),
    enabled: !!server && !!connector && !!pair,
    refetchInterval: 5000,
  });

  const direction =
    price && prevPriceRef.current
      ? price.mid_price > prevPriceRef.current
        ? "up"
        : price.mid_price < prevPriceRef.current
          ? "down"
          : "flat"
      : "flat";

  useEffect(() => {
    if (price?.mid_price) prevPriceRef.current = price.mid_price;
  }, [price?.mid_price]);

  if (!price || !pair) return null;

  const spread = price.best_ask && price.best_bid
    ? price.best_ask - price.best_bid
    : 0;
  const mid = (price.best_ask + price.best_bid) / 2;
  const spreadPct = mid > 0 ? (spread / mid) * 100 : 0;

  const dirColor =
    direction === "up"
      ? "text-[var(--color-green)]"
      : direction === "down"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-text)]";

  return (
    <div className="flex items-center gap-5">
      {/* Mark price */}
      <div>
        <p className={`text-lg font-bold tabular-nums leading-tight ${dirColor}`}>
          {price.mid_price.toLocaleString("en-US", { maximumFractionDigits: 8 })}
        </p>
      </div>

      {price.best_bid > 0 && (
        <>
          {/* Bid */}
          <div className="hidden sm:block">
            <p className="text-[10px] leading-tight text-[var(--color-text-muted)]">Bid</p>
            <p className="text-xs font-medium tabular-nums leading-tight text-[var(--color-green)]">
              {price.best_bid.toLocaleString("en-US", { maximumFractionDigits: 8 })}
            </p>
          </div>

          {/* Ask */}
          <div className="hidden sm:block">
            <p className="text-[10px] leading-tight text-[var(--color-text-muted)]">Ask</p>
            <p className="text-xs font-medium tabular-nums leading-tight text-[var(--color-red)]">
              {price.best_ask.toLocaleString("en-US", { maximumFractionDigits: 8 })}
            </p>
          </div>

          {/* Spread */}
          <div className="hidden md:block">
            <p className="text-[10px] leading-tight text-[var(--color-text-muted)]">Spread</p>
            <p className="text-xs font-medium tabular-nums leading-tight text-[var(--color-text)]">
              {spreadPct.toFixed(3)}%
            </p>
          </div>
        </>
      )}
    </div>
  );
}
