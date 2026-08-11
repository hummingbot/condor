import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

interface ExchangeSelectorProps {
  connectors: string[];
  value: string;
  onChange: (v: string) => void;
  /** Connectors that are gateway DEX networks — listed under their own heading. */
  dexConnectors?: string[];
}

/**
 * Format a gateway network id the way the Telegram surface does, so the two read
 * alike: `solana-mainnet-beta` → `Solana`, `solana-devnet` → `Solana Dev`.
 * Mirrors `utils/telegram_formatters.format_network_display`.
 */
function formatNetwork(id: string) {
  const parts = id.split("-");
  const chain = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
  if (parts.length === 1) return chain;
  const net = parts[1];
  if (net === "mainnet") return chain;
  if (net === "devnet") return `${chain} Dev`;
  if (net === "testnet") return `${chain} Test`;
  return `${chain} ${net.slice(0, 4)}`;
}

// Format connector name for display (e.g. "binance_perpetual" -> "Binance Perp")
function formatName(name: string, isDex = false) {
  if (isDex) return formatNetwork(name);
  return name
    .replace(/_perpetual$/, " perp")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ExchangeSelector({
  connectors,
  value,
  onChange,
  dexConnectors = [],
}: ExchangeSelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const dexSet = new Set(dexConnectors);
  const cexList = connectors.filter((c) => !dexSet.has(c));
  const dexList = connectors.filter((c) => dexSet.has(c));

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
        <span className="font-medium text-[var(--color-primary)]">{formatName(value, dexSet.has(value))}</span>
        <ChevronDown className={`h-3 w-3 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-px min-w-[180px] overflow-hidden rounded-b-lg border border-t-0 border-[var(--color-border)] bg-[var(--color-surface)] shadow-xl shadow-black/30">
          <div className="max-h-72 overflow-y-auto py-1">
            {/* Headings only earn their space once there is more than one group. */}
            {dexList.length > 0 && cexList.length > 0 && <GroupHeading>CEX</GroupHeading>}
            {cexList.map((c) => (
              <ConnectorOption key={c} name={c} value={value} onSelect={onChange} onClose={() => setOpen(false)} />
            ))}
            {dexList.length > 0 && cexList.length > 0 && <GroupHeading>DEX</GroupHeading>}
            {dexList.map((c) => (
              <ConnectorOption key={c} name={c} value={value} isDex onSelect={onChange} onClose={() => setOpen(false)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function GroupHeading({ children }: { children: ReactNode }) {
  return (
    <p className="px-3 pb-0.5 pt-1.5 text-[9px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
      {children}
    </p>
  );
}

function ConnectorOption({
  name,
  value,
  isDex = false,
  onSelect,
  onClose,
}: {
  name: string;
  value: string;
  isDex?: boolean;
  onSelect: (v: string) => void;
  onClose: () => void;
}) {
  return (
    <button
      onClick={() => { onSelect(name); onClose(); }}
      className={`flex w-full items-center px-3 py-1.5 text-left text-xs transition-colors ${
        name === value
          ? "bg-[var(--color-primary)]/10 font-medium text-[var(--color-primary)]"
          : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      {formatName(name, isDex)}
    </button>
  );
}
