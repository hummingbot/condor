import { Maximize2, Minimize2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useEscapeKey } from "@/hooks/useEscapeKey";

/** Remembers the last zen choice, so a reader who wants the whole window keeps it. */
const ZEN_KEY = "condor_sheet_zen";

function readZen(fallback: boolean): boolean {
  const stored = localStorage.getItem(ZEN_KEY);
  return stored === null ? fallback : stored === "1";
}

/**
 * The dock's read view: one full-screen overlay for whatever a dock row points
 * at — a delegation's result, a routine run's report.
 *
 * The dock is 300px wide and deliberately terse; anything worth reading in full
 * opens here instead of squeezing into the column.
 *
 * Two sizes. The windowed one floats over the chat, which is right for a result
 * you glance at and dismiss. Zen takes the entire viewport — no backdrop, no
 * rounding, no inset — because a report is a page laid out for a page's width,
 * and 90vh of it behind a border reads as a cramped preview of the real thing.
 */
export function WorkspaceSheet({
  title,
  subtitle,
  onClose,
  bleed = false,
  defaultZen = false,
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
  /** Open at full viewport, for content that wants the room (reports). */
  defaultZen?: boolean;
  children: React.ReactNode;
}) {
  const [zen, setZen] = useState(() => readZen(defaultZen));

  useEscapeKey(true, onClose);

  // `f` toggles, matching the report browser's own fullscreen key. Ignored while
  // a field has focus — the chat composer is still mounted behind the sheet.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "f" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      )
        return;
      setZen((z) => {
        localStorage.setItem(ZEN_KEY, z ? "0" : "1");
        return !z;
      });
      e.preventDefault();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const toggleZen = () =>
    setZen((z) => {
      localStorage.setItem(ZEN_KEY, z ? "0" : "1");
      return !z;
    });

  return (
    <div
      className={`fixed inset-0 z-50 flex ${
        zen ? "" : "items-center justify-center p-4"
      }`}
    >
      {!zen && (
        <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      )}
      {/* Prose reads badly past a measure, so text stops at `5xl`. A report was
          laid out for a page of its own and gets the whole window. */}
      <div
        className={
          zen
            ? "relative z-10 flex h-full w-full flex-col bg-[var(--color-bg)]"
            : `relative z-10 flex h-[90vh] w-[95vw] flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl ${
                bleed ? "" : "max-w-5xl"
              }`
        }
      >
        <div
          className={`flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border)] ${
            zen ? "px-4 py-2" : "px-6 py-3"
          }`}
        >
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
          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={toggleZen}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title={zen ? "Exit full screen (f)" : "Full screen (f)"}
            >
              {zen ? (
                <Minimize2 className="h-4 w-4" />
              ) : (
                <Maximize2 className="h-4 w-4" />
              )}
            </button>
            <button
              onClick={onClose}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        {/* Zen drops the sheet's max width, which is right for a report and
            wrong for prose — so text keeps its measure by centering instead. */}
        <div
          className={
            bleed
              ? "flex min-h-0 flex-1 flex-col overflow-hidden"
              : `min-h-0 flex-1 overflow-auto px-6 py-4 ${
                  zen ? "mx-auto w-full max-w-5xl" : ""
                }`
          }
        >
          {children}
        </div>
      </div>
    </div>
  );
}
