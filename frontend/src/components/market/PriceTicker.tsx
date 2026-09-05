import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { candleChannelKey, candleStore } from "@/lib/candle-store";
import { formatCompactVolume } from "@/lib/formatters";
import { changeColumnLabel, changeWindowTitle, formatChange } from "@/lib/marketChange";
import { useTickers } from "@/components/market/useTickers";

interface PriceTickerProps {
  server: string;
  connector: string;
  pair: string;
  /** Candle interval to track — defaults to "1m" for most responsive updates */
  interval?: string;
  /**
   * Whether `/market/prices` answers for this connector. False for gateway DEX
   * networks, where the candle close is the only price and the REST call would
   * only 502; bid/ask/spread are then simply absent.
   */
  hasRestPrice?: boolean;
  /**
   * Whether to carry the 24h stats (change, volume) beside the price. Off by
   * default: they cost a whole-connector ticker fetch, which only the trade
   * header — where the market is the subject of the page — is worth paying.
   */
  showStats?: boolean;
}

/**
 * One reading in the header: what it is on top, what it says below.
 *
 * Every number in this strip is a column of the same shape, so the eye reads
 * the labels as one row and the values as another instead of decoding each
 * pair separately — the layout every derivatives venue converged on.
 */
function Stat({
  label,
  title,
  valueClass = "text-[var(--color-text)]",
  className = "",
  children,
}: {
  label: string;
  title?: string;
  valueClass?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={className} title={title}>
      <p className="text-[10px] leading-tight text-[var(--color-text-muted)]">{label}</p>
      <p className={`text-xs font-semibold tabular-nums leading-tight ${valueClass}`}>{children}</p>
    </div>
  );
}

export function PriceTicker({
  server,
  connector,
  pair,
  interval = "1m",
  hasRestPrice = true,
  showStats = false,
}: PriceTickerProps) {
  const prevPriceRef = useRef<number>(0);
  const [candlePrice, setCandlePrice] = useState<number>(0);

  // Subscribe to candle store for real-time last close price
  useEffect(() => {
    if (!server || !connector || !pair) {
      setCandlePrice(0);
      prevPriceRef.current = 0;
      return;
    }

    const key = candleChannelKey(server, connector, pair, interval);

    // Seed from cache unconditionally: an empty cache for the newly subscribed
    // channel must clear the previous market's close, or `displayPrice` below
    // keeps preferring it over the REST mid_price this pair already resolved.
    const cached = candleStore.subscribe(key);
    setCandlePrice(cached[cached.length - 1]?.close ?? 0);
    prevPriceRef.current = 0; // never colour a new pair against the old one's price

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
    enabled: !!server && !!connector && !!pair && hasRestPrice,
    refetchInterval: 15_000,
  });

  // 24h stats for this pair, off the connector-wide ticker snapshot the market
  // browser already caches — same query key, so opening the browser costs
  // nothing extra and this strip inherits its 60s refresh.
  const { byPair } = useTickers(server, connector, showStats && hasRestPrice);
  const ticker = showStats ? byPair.get(pair) : undefined;

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

  const windowS = ticker?.change_window_s ?? null;

  return (
    <div className="flex items-center gap-5">
      {/* Mark: the one value the page is about, so it keeps the size and the
          tick colour. It still wears a label, so the row of headings above the
          numbers is unbroken. */}
      <div>
        <p className="text-[10px] leading-tight text-[var(--color-text-muted)]">Mark</p>
        <p className={`text-base font-bold tabular-nums leading-tight ${dirColor}`}>
          {displayPrice.toLocaleString("en-US", { maximumFractionDigits: 8 })}
        </p>
      </div>

      {price && price.best_bid > 0 && (
        <>
          <Stat label="Bid" valueClass="text-[var(--color-green)]" className="hidden sm:block">
            {price.best_bid.toLocaleString("en-US", { maximumFractionDigits: 8 })}
          </Stat>

          <Stat label="Ask" valueClass="text-[var(--color-red)]" className="hidden sm:block">
            {price.best_ask.toLocaleString("en-US", { maximumFractionDigits: 8 })}
          </Stat>

          <Stat label="Spread" className="hidden md:block">
            {spreadPct.toFixed(3)}%
          </Stat>
        </>
      )}

      {ticker && (
        <>
          {/* The change label states its own window — the backend measures
              against its hourly snapshots, so it is only "24h" when it really
              is 24h. Same rule as the market browser's column.

              Absent when there is no reference snapshot yet. The browser's
              table keeps an empty cell to hold its column together; a header
              strip has no such obligation, and "Δ —" would only spend the
              width on saying nothing. */}
          {ticker.change_pct != null && (
            <Stat
              label={changeColumnLabel(windowS)}
              title={changeWindowTitle(windowS)}
              valueClass={
                ticker.change_pct >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"
              }
              className="hidden md:block"
            >
              {formatChange(ticker.change_pct)}
            </Stat>
          )}

          <Stat
            label="24h Volume"
            title={
              ticker.usd_volume != null
                ? `$${ticker.usd_volume.toLocaleString("en-US", { maximumFractionDigits: 2 })} traded in 24h`
                : "The quote asset could not be priced in USD"
            }
            className="hidden lg:block"
          >
            {ticker.usd_volume != null ? formatCompactVolume(ticker.usd_volume) : "—"}
          </Stat>
        </>
      )}
    </div>
  );
}
