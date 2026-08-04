import { X } from "lucide-react";

import { useEscapeKey } from "@/hooks/useEscapeKey";

/**
 * The dock's read view: one full-screen overlay for whatever a dock row points
 * at — a delegation's result, a routine run's report.
 *
 * The dock is 300px wide and deliberately terse; anything worth reading in full
 * opens here instead of squeezing into the column.
 */
export function WorkspaceSheet({
  title,
  subtitle,
  onClose,
  bleed = false,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  /**
   * Give the body the whole sheet — no padding, no scroll of its own. For
   * content that brings its own page, i.e. a report's iframe.
   */
  bleed?: boolean;
  children: React.ReactNode;
}) {
  useEscapeKey(true, onClose);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      {/* Prose reads badly past a measure, so text stops at `5xl`. A report was
          laid out for a page of its own and gets the whole window. */}
      <div
        className={`relative z-10 flex h-[90vh] w-[95vw] flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl ${
          bleed ? "" : "max-w-5xl"
        }`}
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border)] px-6 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-[var(--color-text)]">
              {title}
            </h2>
            {subtitle && (
              <p className="truncate text-[11px] text-[var(--color-text-muted)]">
                {subtitle}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title="Close (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div
          className={
            bleed
              ? "flex min-h-0 flex-1 flex-col overflow-hidden"
              : "min-h-0 flex-1 overflow-auto px-6 py-4"
          }
        >
          {children}
        </div>
      </div>
    </div>
  );
}
