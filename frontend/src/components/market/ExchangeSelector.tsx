import { useCallback, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { formatConnectorName } from "@/lib/formatters";

/**
 * Every venue this selector lists has an order book. Gateway networks live on
 * the DEX page, where the pool — not the venue — is the unit of navigation, so
 * there is no group for them here.
 *
 * The one split that *is* worth heading is credentials (ARCH-272): most of this
 * list is chartable but not tradable, and that difference decides whether the
 * Execute panel opens or goes view-only. A group header says so the first time
 * it is seen and hides nothing, which a filter toggle does neither of. A group
 * with no members is not headed at all — on a server where every venue is
 * credentialed the list looks exactly as it did before.
 */
interface ExchangeSelectorProps {
  connectors: string[];
  /**
   * The subset of `connectors` the account holds keys on; the rest are listed
   * under `View only`. Omitted means "no idea yet" — before the venues query
   * resolves nothing is known to be view-only, and a flat list is the honest
   * rendering as well as the one that does not flicker.
   */
  credentialed?: ReadonlySet<string>;
  value: string;
  onChange: (v: string) => void;
}

export function ExchangeSelector({
  connectors,
  credentialed,
  value,
  onChange,
}: ExchangeSelectorProps) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  // `connectors` already arrives credentialed-first (see `orderBookVenues`), so
  // this is a partition that preserves the caller's order, never a re-sort.
  const [mine, viewOnly] = useMemo(() => {
    if (!credentialed) return [connectors, [] as string[]];
    return [
      connectors.filter((c) => credentialed.has(c)),
      connectors.filter((c) => !credentialed.has(c)),
    ];
  }, [connectors, credentialed]);

  // Headers only earn their room when there is something to tell apart.
  const grouped = mine.length > 0 && viewOnly.length > 0;

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex items-center gap-1.5 px-3 py-2.5 text-xs transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        {/* The pair beside this is the identity of everything below; the venue
            is its qualifier, so it stays body text. The accent colour in this
            bar is reserved for state — the open Browse button, the browser's
            selected row, this menu's selected option. */}
        <span className="font-medium text-[var(--color-text)]">{formatConnectorName(value)}</span>
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
        <ConnectorGroup
          label="Your accounts"
          heading={grouped}
          names={mine}
          value={value}
          onSelect={onChange}
          onClose={close}
        />
        <ConnectorGroup
          label="View only"
          heading={grouped}
          names={viewOnly}
          value={value}
          onSelect={onChange}
          onClose={close}
        />
      </AnchoredMenu>
    </>
  );
}

/**
 * One headed run of options. `role="group"` inside the listbox is what keeps the
 * header out of the option sequence for a screen reader while still naming the
 * run it labels; an empty group renders nothing at all rather than a bare header.
 */
function ConnectorGroup({
  label,
  heading,
  names,
  value,
  onSelect,
  onClose,
}: {
  label: string;
  heading: boolean;
  names: string[];
  value: string;
  onSelect: (v: string) => void;
  onClose: () => void;
}) {
  if (!names.length) return null;
  return (
    <div role="group" aria-label={label}>
      {heading && (
        <div className="px-3 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          {label}
        </div>
      )}
      {names.map((c) => (
        <ConnectorOption key={c} name={c} value={value} onSelect={onSelect} onClose={onClose} />
      ))}
    </div>
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
      {formatConnectorName(name)}
    </button>
  );
}
