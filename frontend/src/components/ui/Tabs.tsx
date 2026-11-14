import type { ReactNode } from "react";

/**
 * Switch between whole panels of content (Assets / Positions, Execute / Data,
 * Brain / Skills / Memories). Underlined, sits on the edge of the panel it
 * controls. To filter a view in place use `SegmentedControl`.
 */
export interface TabItem<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
  count?: number;
}

interface Props<T extends string> {
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Draw the bottom hairline across the full width. */
  bordered?: boolean;
  className?: string;
}

export function Tabs<T extends string>({ items, value, onChange, bordered = true, className = "" }: Props<T>) {
  return (
    <div
      role="tablist"
      className={`flex items-center gap-4 overflow-x-auto overflow-y-hidden [scrollbar-width:none] ${bordered ? "border-b border-[var(--color-border)]" : ""} ${className}`}
    >
      {items.map((t) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.value)}
            className={`-mb-px inline-flex h-9 shrink-0 items-center gap-1.5 border-b-2 text-md font-medium transition-colors [&_svg]:h-3.5 [&_svg]:w-3.5 ${
              active
                ? "border-[var(--color-primary)] text-[var(--color-text)]"
                : "border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            {t.icon}
            {t.label}
            {t.count !== undefined && (
              <span className="rounded-full bg-[var(--color-surface-hover)] px-1.5 text-2xs tabular-nums text-[var(--color-text-muted)]">
                {t.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
