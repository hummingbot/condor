// Class builder for the shared Button, split out so non-<button> elements
// (router <Link>, <a>) can look like buttons too. See Button.tsx.

export type ButtonVariant = "primary" | "secondary" | "soft" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg" | "icon";

const BASE =
  // Mirrors @hummingbot/design-system buttonVariants (shadcn conventions).
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]/50 " +
  "focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--color-bg)] " +
  "disabled:pointer-events-none disabled:opacity-50 [&_svg]:h-3.5 [&_svg]:w-3.5 [&_svg]:shrink-0";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--color-primary-fill)] text-[var(--on-primary)] shadow-sm hover:bg-[var(--color-primary-fill)]/90",
  secondary:
    "border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm " +
    "hover:bg-[var(--color-surface-hover)]",
  soft:
    "bg-[var(--color-primary-fill)]/12 text-[var(--color-primary)] hover:bg-[var(--color-primary-fill)]/20",
  ghost:
    "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]",
  danger:
    "bg-[var(--color-red)] text-white shadow-sm hover:bg-[var(--color-red)]/90",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-4 text-sm [&_svg]:h-4 [&_svg]:w-4",
  lg: "h-10 px-6 text-sm font-semibold [&_svg]:h-4 [&_svg]:w-4",
  // Square, icon-only. Always pass an aria-label.
  icon: "h-9 w-9 [&_svg]:h-4 [&_svg]:w-4",
};

export function buttonClass(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  extra = "",
): string {
  return `${BASE} ${VARIANTS[variant]} ${SIZES[size]} ${extra}`.trim();
}
