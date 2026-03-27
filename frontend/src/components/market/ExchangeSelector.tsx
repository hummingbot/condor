import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

interface ExchangeSelectorProps {
  connectors: string[];
  value: string;
  onChange: (v: string) => void;
}

// Format connector name for display (e.g. "binance_perpetual" -> "Binance Perp")
function formatName(name: string) {
  return name
    .replace(/_perpetual$/, " perp")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ExchangeSelector({
  connectors,
  value,
  onChange,
}: ExchangeSelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-3 py-2.5 text-xs transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <span className="font-medium text-[var(--color-primary)]">{formatName(value)}</span>
        <ChevronDown className={`h-3 w-3 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-px min-w-[180px] overflow-hidden rounded-b-lg border border-t-0 border-[var(--color-border)] bg-[var(--color-surface)] shadow-xl shadow-black/30">
          <div className="max-h-72 overflow-y-auto py-1">
            {connectors.map((c) => (
              <button
                key={c}
                onClick={() => { onChange(c); setOpen(false); }}
                className={`flex w-full items-center px-3 py-1.5 text-left text-xs transition-colors ${
                  c === value
                    ? "bg-[var(--color-primary)]/10 font-medium text-[var(--color-primary)]"
                    : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                {formatName(c)}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
