import type { ReactNode } from "react";

/**
 * Status and metadata pills — @hummingbot/design-system `Badge` shape (rounded-full,
 * border, text-xs semibold) in tinted tones, which are its `alertColors` pattern. Tones map to theme tokens only, so they follow
 * light / dark / colour-blind themes — never `bg-purple-100` and friends.
 */
export type BadgeTone = "neutral" | "primary" | "success" | "danger" | "warning";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] border-[var(--color-border)]",
  primary: "bg-[var(--color-primary-fill)]/10 text-[var(--color-primary)] border-[var(--color-primary)]/25",
  success: "bg-[var(--color-green)]/10 text-[var(--color-green)] border-[var(--color-green)]/25",
  danger: "bg-[var(--color-red)]/10 text-[var(--color-red)] border-[var(--color-red)]/25",
  warning: "bg-[var(--color-yellow)]/10 text-[var(--color-yellow)] border-[var(--color-yellow)]/25",
};

export function Badge({
  tone = "neutral",
  dot = false,
  icon,
  children,
  className = "",
}: {
  tone?: BadgeTone;
  /** Leading status dot in the tone colour. */
  dot?: boolean;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold [&_svg]:h-3 [&_svg]:w-3 ${TONES[tone]} ${className}`}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {icon}
      {children}
    </span>
  );
}

/** Coloured dot for live / idle / error states next to a name. */
export function StatusDot({ tone = "neutral", pulse = false }: { tone?: BadgeTone; pulse?: boolean }) {
  const color = {
    neutral: "bg-[var(--color-text-muted)]",
    primary: "bg-[var(--color-primary-fill)]",
    success: "bg-[var(--color-green)]",
    danger: "bg-[var(--color-red)]",
    warning: "bg-[var(--color-yellow)]",
  }[tone];
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0">
      {pulse && <span className={`absolute inset-0 animate-ping rounded-full opacity-60 ${color}`} />}
      <span className={`relative h-2 w-2 rounded-full ${color}`} />
    </span>
  );
}
