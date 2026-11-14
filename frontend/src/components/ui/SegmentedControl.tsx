import type { ReactNode } from "react";

/**
 * Pick one of a few mutually exclusive options that filter or switch a view in
 * place: timeframes (1D/1W/1M), Running/Terminated, Both/Controllers/Executors.
 * For switching between whole panels use `Tabs` instead.
 */
export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
  /** Small count shown after the label. */
  count?: number;
  disabled?: boolean;
}

interface Props<T extends string> {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  size?: "sm" | "md";
  /** Stretch segments to fill the container width. */
  fullWidth?: boolean;
  className?: string;
  "aria-label"?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  size = "md",
  fullWidth = false,
  className = "",
  ...aria
}: Props<T>) {
  const seg = size === "sm" ? "h-6 px-2 text-xs" : "h-7 px-2.5 text-md";
  return (
    <div
      role="radiogroup"
      aria-label={aria["aria-label"]}
      className={`${fullWidth ? "flex w-full" : "inline-flex"} items-center gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5 ${className}`}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={o.disabled}
            onClick={() => onChange(o.value)}
            className={`${seg} ${fullWidth ? "flex-1" : ""} inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 [&_svg]:h-3.5 [&_svg]:w-3.5 ${
              active
                ? "bg-[var(--color-surface)] text-[var(--color-text)] shadow-sm ring-1 ring-[var(--color-border)]"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            {o.icon}
            {o.label}
            {o.count !== undefined && (
              <span className={`tabular-nums ${active ? "text-[var(--color-primary)]" : "opacity-70"}`}>
                {o.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
