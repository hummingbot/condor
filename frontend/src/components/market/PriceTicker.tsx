import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { candleStore } from "@/lib/candle-store";

interface PriceTickerProps {
  server: string;
  connector: string;
  pair: string;
  /** Candle interval to track — defaults to "1m" for most responsive updates */
  interval?: string;
}

export function PriceTicker({ server, connector, pair, interval = "1m" }: PriceTickerProps) {
  const prevPriceRef = useRef<number>(0);
  const [candlePrice, setCandlePrice] = useState<number>(0);

  // Subscribe to candle store for real-time last close price
  useEffect(() => {
    if (!server || !connector || !pair) {
      setCandlePrice(0);
      return;
    }

    const key = `candles:${server}:${connector}:${pair}:${interval}`;

    // Check existing cached candles
    const cached = candleStore.subscribe(key);
    if (cached.length > 0) {
      setCandlePrice(cached[cached.length - 1].close);
    }

    const removeListener = candleStore.onUpdate(key, (candles) => {
      if (candles.length > 0) {
        setCandlePrice(candles[candles.length - 1].close);
      }
    });

    return () => {
      removeListener();
      candleStore.unsubscribe(key);
    };
  }, [server, connector, pair, interval]);

  // REST fallback for bid/ask/spread (less frequent)
  const { data: price } = useQuery({
    queryKey: ["price", server, connector, pair],
    queryFn: () => api.getPrice(server, connector, pair),
    enabled: !!server && !!connector && !!pair,
    refetchInterval: 15_000,
  });

  // Use candle close as primary price, fall back to REST mid_price
  const displayPrice = candlePrice > 0 ? candlePrice : (price?.mid_price ?? 0);

  const direction =
    displayPrice && prevPriceRef.current
      ? displayPrice > prevPriceRef.current
        ? "up"
        : displayPrice < prevPriceRef.current
          ? "down"
          : "flat"
      : "flat";

  useEffect(() => {
    if (displayPrice > 0) prevPriceRef.current = displayPrice;
  }, [displayPrice]);

  if (!displayPrice || !pair) return null;

  const spread = price?.best_ask && price?.best_bid
    ? price.best_ask - price.best_bid
    : 0;
  const mid = price ? (price.best_ask + price.best_bid) / 2 : 0;
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
          {displayPrice.toLocaleString("en-US", { maximumFractionDigits: 8 })}
        </p>
      </div>

      {price && price.best_bid > 0 && (
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
