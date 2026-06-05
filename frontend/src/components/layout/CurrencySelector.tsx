import { ChevronDown, DollarSign } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  useDisplayCurrency,
  CURRENCY_OPTIONS,
  CURRENCY_SYMBOLS,
  type DisplayCurrency,
} from "@/hooks/useDisplayCurrency";

export function CurrencySelector() {
  const { currency, setCurrency } = useDisplayCurrency();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm hover:bg-[var(--color-surface-hover)] transition-colors"
        title="Display currency"
      >
        <DollarSign className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <span className="text-xs font-medium">{currency}</span>
        <ChevronDown
          className={`h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1.5 min-w-[140px] rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-1 shadow-xl">
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
        </div>
      )}
    </div>
  );
}
