import { PairLabel } from "@/components/executor/PairLabel";
import type { PositionHeld } from "@/lib/api";
import { formatCurrencyPnl, pnlTextClass } from "@/lib/formatters";

/**
 * The positions a run's controllers still hold on the server — the card
 * `SessionExecutors` draws above its charts. The caller filters them to the
 * run's controller ids; an empty list renders nothing.
 */
export function SessionPositions({ positions }: { positions: PositionHeld[] }) {
  if (positions.length === 0) return null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        Positions Held ({positions.length})
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
              <th className="pb-2 pr-3">Pair</th>
              <th className="pb-2 pr-3">Side</th>
              <th className="pb-2 pr-3 text-right">Amount</th>
              <th className="pb-2 pr-3 text-right">Entry</th>
              <th className="pb-2 pr-3 text-right">Current</th>
              <th className="pb-2 pr-3 text-right">Unreal. PnL</th>
              <th className="pb-2 text-right">Leverage</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => {
              const upnl = p.unrealized_pnl_quote ?? p.unrealized_pnl ?? 0;
              const side = p.position_side || p.side || "—";
              const amount = p.net_amount_base ?? p.amount ?? 0;
              const entry = p.buy_breakeven_price ?? p.entry_price ?? 0;
              const current = p.current_price ?? 0;
              return (
                <tr key={`${p.trading_pair}-${i}`} className="border-b border-[var(--color-border)]/30">
                  <td className="py-2 pr-3 font-mono text-[var(--color-text)]">
                    <PairLabel tradingPair={p.trading_pair} connector={p.connector_name} />
                  </td>
                  <td className="py-2 pr-3">
                    <span className={side.toLowerCase().includes("long") || side.toLowerCase() === "buy" ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}>
                      {side.toUpperCase()}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-[var(--color-text)]">{Math.abs(amount).toFixed(4)}</td>
                  <td className="py-2 pr-3 text-right font-mono text-[var(--color-text-muted)]">${entry.toFixed(2)}</td>
                  <td className="py-2 pr-3 text-right font-mono text-[var(--color-text)]">${current.toFixed(2)}</td>
                  <td className={`py-2 pr-3 text-right font-mono ${pnlTextClass(upnl)}`}>
                    {formatCurrencyPnl(upnl)}
                  </td>
                  <td className="py-2 text-right font-mono text-[var(--color-text-muted)]">{p.leverage ? `${p.leverage}x` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
