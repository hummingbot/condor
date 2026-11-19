import { forwardRef, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";

const CONTROL =
  "h-8 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 text-md text-[var(--color-text)] " +
  "placeholder:text-[var(--color-text-muted)]/70 transition-colors hover:border-[var(--color-border-hover)] " +
  "focus:border-[var(--color-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]/20 " +
  "disabled:cursor-not-allowed disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { icon?: ReactNode }>(
  function Input({ className = "", icon, ...rest }, ref) {
    if (!icon) return <input ref={ref} className={`${CONTROL} ${className}`} {...rest} />;
    return (
      <div className="relative">
        <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] [&_svg]:h-3.5 [&_svg]:w-3.5">
          {icon}
        </span>
        <input ref={ref} className={`${CONTROL} pl-8 ${className}`} {...rest} />
      </div>
    );
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className = "", children, ...rest },
  ref,
) {
  return (
    <div className="relative">
      <select ref={ref} className={`${CONTROL} appearance-none pr-8 ${className}`} {...rest}>
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
    </div>
  );
});

/**
 * Label + control + hint/error, stacked and left-aligned. Long descriptions go
 * in `hint` below the control, not in the label.
 */
export function Field({
  label,
  hint,
  error,
  htmlFor,
  children,
  className = "",
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex min-w-0 flex-col gap-1.5 ${className}`}>
      <label htmlFor={htmlFor} className="text-xs font-medium text-[var(--color-text)]">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-[var(--color-red)]">{error}</p>
      ) : hint ? (
        <p className="text-xs leading-snug text-[var(--color-text-muted)]">{hint}</p>
      ) : null}
    </div>
  );
}
