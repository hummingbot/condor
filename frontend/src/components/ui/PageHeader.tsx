import type { ReactNode } from "react";

/**
 * Page header — the @hummingbot/design-system `adminPageHeader` preset:
 * eyebrow, bold title, muted description, actions on the right.
 * Pages with their own sidebar layout (Agents, Bots, Routines) use `compact`
 * at the top of their main pane.
 */
export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  compact = false,
  className = "",
}: {
  title: ReactNode;
  description?: ReactNode;
  /** Small mono uppercase kicker above the title, e.g. the section name. */
  eyebrow?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
  className?: string;
}) {
  return (
    <header className={`flex items-start justify-between gap-4 ${compact ? "" : "mb-6"} ${className}`}>
      <div className="min-w-0">
        {eyebrow && <p className={`eyebrow ${compact ? "mb-1" : "mb-2"}`}>{eyebrow}</p>}
        <h1
          className={`truncate font-bold tracking-tight text-[var(--color-text)] ${
            compact ? "text-lg" : "text-2xl md:text-3xl"
          }`}
        >
          {title}
        </h1>
        {description && (
          <p className={`text-[var(--color-text-muted)] ${compact ? "text-xs" : "mt-1"}`}>{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}
