import { ChevronDown, DollarSign } from "lucide-react";
import { useCallback, useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import {
  useDisplayCurrency,
  CURRENCY_OPTIONS,
  CURRENCY_SYMBOLS,
  type DisplayCurrency,
} from "@/hooks/useDisplayCurrency";

export function CurrencySelector() {
  const { currency, setCurrency } = useDisplayCurrency();
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm hover:bg-[var(--color-surface-hover)] transition-colors"
        title="Display currency"
      >
        <DollarSign className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <span className="text-xs font-medium">{currency}</span>
        <ChevronDown
          className={`h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={close}
        align="right"
        className="min-w-[140px] py-1"
      >
        <div className="px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Display Currency
        </div>
        {CURRENCY_OPTIONS.map((c: DisplayCurrency) => (
          <button
            key={c}
            onClick={() => {
              setCurrency(c);
              setOpen(false);
            }}
            className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-sm transition-colors hover:bg-[var(--color-surface-hover)] ${
              c === currency
                ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                : ""
            }`}
          >
            <span className="w-5 text-center text-xs text-[var(--color-text-muted)]">
              {CURRENCY_SYMBOLS[c]}
            </span>
            <span>{c}</span>
            {c === currency && (
              <span className="ml-auto text-[10px] font-medium text-[var(--color-primary)]">
                ✓
              </span>
            )}
          </button>
        ))}
      </AnchoredMenu>
    </>
  );
}
