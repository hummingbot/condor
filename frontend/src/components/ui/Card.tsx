import type { HTMLAttributes, ReactNode } from "react";

/**
 * A bordered surface — @hummingbot/design-system `Card` (rounded-xl, border,
 * card background, soft shadow). Padding is opt-out so tables can run edge to
 * edge.
 */
export function Card({
  padded = true,
  className = "",
  ...rest
}: HTMLAttributes<HTMLDivElement> & { padded?: boolean }) {
  return (
    <div
      className={`rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] shadow-sm ${padded ? "p-6" : ""} ${className}`}
      {...rest}
    />
  );
}

/** Title row of a card: optional eyebrow, title, description; actions on the right. */
export function CardHeader({
  title,
  description,
  eyebrow,
  icon,
  actions,
  className = "",
}: {
  title: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mb-4 flex items-start justify-between gap-3 ${className}`}>
      <div className="min-w-0 space-y-1.5">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h3 className="flex items-center gap-2 font-semibold leading-none tracking-tight text-[var(--color-text)] [&_svg]:h-4 [&_svg]:w-4 [&_svg]:text-[var(--color-text-muted)]">
          {icon}
          <span className="truncate">{title}</span>
        </h3>
        {description && <p className="text-sm text-[var(--color-text-muted)]">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/**
 * The DS eyebrow: small mono uppercase gold kicker. Use above page headers,
 * major sections and sidebar groups.
 */
export function SectionLabel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <p className={`eyebrow ${className}`}>{children}</p>;
}
