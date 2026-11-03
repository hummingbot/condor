/**
 * The bubble a one-time hint is shown in — see `hooks/useOneTimeHint.ts`, which
 * owns when it comes up and when it stops coming up for good.
 */

import type { ReactNode } from "react";

/**
 * The bubble itself, positioned by the caller against a `relative` anchor.
 *
 * `pointer-events-none` so it can never take the hover that is keeping it up,
 * and `role="status"` because for the length of its life it is the only place
 * the shortcut is written down — the anchor's `title` is suppressed while the
 * hint is pending.
 */
export function HintBubble({
  children,
  className = "left-2 top-full mt-1",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      role="status"
      className={`pointer-events-none absolute z-30 whitespace-nowrap rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[11px] text-[var(--color-text)] shadow-lg ${className}`}
    >
      {children}
    </div>
  );
}
