import { Check, ChevronDown, X } from "lucide-react";
import { useCallback, useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";

/**
 * A dropdown that ticks several of its options rather than choosing one.
 *
 * Lifted out of the executors page so the performance browser's sidebar can
 * carry the same type and controller filters it had (FEAT-086).
 */
export function MultiSelect({
  options,
  selected,
  onChange,
  placeholder,
  label,
}: {
  options: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
  placeholder: string;
  label?: (value: string) => string;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpen(false), []);

  const toggle = (value: string) => {
    if (selected.includes(value)) {
      onChange(selected.filter((v) => v !== value));
    } else {
      onChange([...selected, value]);
    }
  };

  const display = label ?? ((v: string) => v);

  return (
    <>
      <button
        ref={setAnchor}
        type="button"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-left text-sm transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
      >
        <span className="truncate max-w-[180px] text-[var(--color-text)]">
          {selected.length === 0
            ? placeholder
            : selected.length === 1
              ? display(selected[0])
              : `${selected.length} selected`}
        </span>
        {selected.length > 0 && (
          <span
            className="flex h-4 w-4 items-center justify-center rounded-full bg-[var(--color-primary)]/15 text-[10px] font-bold text-[var(--color-primary)]"
          >
            {selected.length}
          </span>
        )}
        <ChevronDown className={`h-3.5 w-3.5 flex-shrink-0 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={close}
        matchAnchorWidth="min"
        maxHeight={256}
        className="w-max"
      >
        {selected.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="flex w-full items-center gap-2 border-b border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] transition-colors"
          >
            <X className="h-3 w-3" />
            Clear all
          </button>
        )}
        {options.map((opt) => {
          const isActive = selected.includes(opt);
          return (
            <button
              key={opt}
              type="button"
              onClick={() => toggle(opt)}
              className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors ${
                isActive
                  ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                  : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <div className={`flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-colors ${
                isActive
                  ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white"
                  : "border-[var(--color-border)]"
              }`}>
                {isActive && <Check className="h-3 w-3" />}
              </div>
              <span className="truncate">{display(opt)}</span>
            </button>
          );
        })}
        {options.length === 0 && (
          <div className="px-3 py-2 text-xs text-[var(--color-text-muted)]">No options</div>
        )}
      </AnchoredMenu>
    </>
  );
}
