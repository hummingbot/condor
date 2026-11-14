import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";

/**
 * What a panel shows when it has nothing to show (@hummingbot/design-system
 * `emptyState` preset): an icon, one line saying
 * what is missing, optionally why, and the action that fixes it.
 * Size it to the content — it should not stretch a 600px box for one sentence.
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  compact = false,
  className = "",
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center text-center ${compact ? "px-4 py-8" : "px-6 py-12"} ${className}`}
    >
      {icon && (
        <div
          className={`text-[var(--color-text-muted)] ${
            compact ? "mb-3 [&_svg]:h-8 [&_svg]:w-8" : "mb-4 [&_svg]:h-12 [&_svg]:w-12"
          }`}
        >
          {icon}
        </div>
      )}
      <p className={`font-medium text-[var(--color-text)] ${compact ? "mb-1 text-sm" : "mb-2 text-lg"}`}>{title}</p>
      {description && (
        <p className={`max-w-sm text-center text-[var(--color-text-muted)] ${compact ? "text-xs" : "text-sm"}`}>
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Inline spinner + optional label, for buttons and small regions. */
export function Spinner({ label, className = "" }: { label?: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 text-xs text-[var(--color-text-muted)] ${className}`}>
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
      {label}
    </span>
  );
}

/** Placeholder block shaped like the content that is loading. Prefer over "Loading..." text. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-[var(--color-surface-hover)] ${className}`} />;
}
