import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useCondorWebSocket } from "@/hooks/useWebSocket";

interface Trade {
  price: number;
  amount: number;
  side: "buy" | "sell";
  timestamp: number;
}

interface RecentTradesProps {
  server: string;
  connector: string;
  pair: string;
}

const MAX_TRADES = 50;

function formatTime(ts: number): string {
  const d = new Date(ts > 1e12 ? ts : ts * 1000);
  return d.toLocaleTimeString("en-US", { hour12: false });
}

function formatPrice(n: number): string {
  if (n >= 1000) return n.toFixed(2);
  if (n >= 1) return n.toFixed(4);
  if (n >= 0.01) return n.toFixed(6);
  return n.toFixed(8);
}

function formatAmount(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 10_000) return (n / 1_000).toFixed(1) + "K";
  return n.toFixed(4);
}

export function RecentTrades({ server, connector, pair }: RecentTradesProps) {
  const [trades, setTrades] = useState<Trade[]>([]);
  const tradesRef = useRef<Trade[]>([]);

  const channel = `trades:${server}:${connector}:${pair}`;
  const channels = useMemo(() => [channel], [channel]);
  const { wsRef, wsVersion } = useCondorWebSocket(channels, server);

  // Reset trades on pair change
  useEffect(() => {
    setTrades([]);
    tradesRef.current = [];
  }, [connector, pair]);

  const handleMessage = useCallback(
    (msgChannel: string, data: unknown) => {
      if (msgChannel !== channel) return;

      const payload = data as {
        type: string;
        data?: Trade[];
      };

      if (payload.type === "trades" && payload.data?.length) {
        const current = tradesRef.current;
        const merged = [...payload.data, ...current].slice(0, MAX_TRADES);
        tradesRef.current = merged;
        setTrades(merged);
      }
    },
    [channel],
  );

  useEffect(() => {
    const ws = wsRef.current;
    if (!ws) return;
    return ws.onMessage(handleMessage);
  }, [wsVersion, handleMessage]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!pair) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-[var(--color-text-muted)]">
        Select a pair
      </div>
    );
  }

  return (
    <div className="flex flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-[var(--color-border)] px-3 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
          Recent Trades
        </h3>
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-3 gap-1 border-b border-[var(--color-border)] px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        <span>Time</span>
        <span className="text-right">Price</span>
        <span className="text-right">Amount</span>
      </div>

      {/* Trades list */}
      <div className="flex-1 overflow-y-auto">
        {trades.length === 0 ? (
          <div className="flex items-center justify-center py-6 text-xs text-[var(--color-text-muted)]">
            Waiting for trades...
          </div>
        ) : (
          trades.map((trade, i) => (
            <div
              key={`${trade.timestamp}-${i}`}
              className="grid grid-cols-3 gap-1 px-3 py-[3px] text-[11px] font-mono tabular-nums hover:bg-[var(--color-surface-hover)]"
            >
              <span className="text-[var(--color-text-muted)]">
                {formatTime(trade.timestamp)}
              </span>
              <span
                className="text-right"
                style={{
                  color:
                    trade.side === "buy"
                      ? "var(--color-green)"
                      : "var(--color-red)",
                }}
              >
                {formatPrice(trade.price)}
              </span>
              <span className="text-right text-[var(--color-text)]">
                {formatAmount(trade.amount)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
