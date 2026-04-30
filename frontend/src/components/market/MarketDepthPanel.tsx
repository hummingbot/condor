import { OrderBook } from "./OrderBook";
import { RecentTrades } from "./RecentTrades";

interface MarketDepthPanelProps {
  server: string;
  connector: string;
  pair: string;
}

export function MarketDepthPanel({ server, connector, pair }: MarketDepthPanelProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-[3] overflow-hidden">
        <OrderBook server={server} connector={connector} pair={pair} />
      </div>
      <div className="flex-[2] overflow-hidden border-t border-[var(--color-border)]">
        <RecentTrades server={server} connector={connector} pair={pair} />
      </div>
    </div>
  );
}
