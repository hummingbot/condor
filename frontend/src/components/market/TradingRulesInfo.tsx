import { Info } from "lucide-react";

import type { TradingRule } from "@/lib/api";

interface TradingRulesInfoProps {
  rule: TradingRule | undefined;
}

function fmtRule(n: number): string {
  if (n === 0) return "—";
  if (n >= 1) return n.toLocaleString("en-US", { maximumFractionDigits: 4 });
  // Show significant digits for small numbers
  return n.toFixed(Math.max(2, -Math.floor(Math.log10(n)) + 2));
}

export function TradingRulesInfo({ rule }: TradingRulesInfoProps) {
  if (!rule) return null;

  return (
    <div className="flex items-center gap-4 border-t border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-2 text-[11px] text-[var(--color-text-muted)]">
      <Info className="h-3 w-3 shrink-0" />
      <div className="flex flex-wrap gap-x-4 gap-y-0.5">
        <span>
          Min Size: <b className="text-[var(--color-text)]">{fmtRule(rule.min_order_size)}</b>
        </span>
        <span>
          Min Notional: <b className="text-[var(--color-text)]">${fmtRule(rule.min_notional_size)}</b>
        </span>
        <span>
          Tick: <b className="text-[var(--color-text)]">{fmtRule(rule.min_price_increment)}</b>
        </span>
        <span>
          Lot: <b className="text-[var(--color-text)]">{fmtRule(rule.min_base_amount_increment)}</b>
        </span>
      </div>
    </div>
  );
}
