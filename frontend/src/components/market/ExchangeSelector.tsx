import { useCallback, useState } from "react";
import { ChevronDown } from "lucide-react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";

/**
 * Every venue this selector lists has an order book. Gateway networks live on
 * the DEX page, where the pool — not the venue — is the unit of navigation, so
 * there is no second group here to head.
 */
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
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex items-center gap-1.5 px-3 py-2.5 text-xs transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <span className="font-medium text-[var(--color-primary)]">{formatName(value)}</span>
        <ChevronDown className={`h-3 w-3 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {/* Drawn as a continuation of the tab it hangs off, hence gap 1 and a
          square top edge — the portal is only there so no header can clip it. */}
      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={close}
        gap={1}
        maxHeight={288}
        role="listbox"
        className="min-w-[180px] rounded-t-none border-t-0 py-1 shadow-black/30"
      >
        {connectors.map((c) => (
          <ConnectorOption key={c} name={c} value={value} onSelect={onChange} onClose={close} />
        ))}
      </AnchoredMenu>
    </>
  );
}

function ConnectorOption({
  name,
  value,
  onSelect,
  onClose,
}: {
  name: string;
  value: string;
  onSelect: (v: string) => void;
  onClose: () => void;
}) {
  return (
    <button
      role="option"
      aria-selected={name === value}
      onClick={() => { onSelect(name); onClose(); }}
      className={`flex w-full items-center px-3 py-1.5 text-left text-xs transition-colors ${
        name === value
          ? "bg-[var(--color-primary)]/10 font-medium text-[var(--color-primary)]"
          : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      {formatName(name)}
    </button>
  );
}
