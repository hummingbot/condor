import {
  ChevronLeft,
  ChevronRight,
  Layers,
  Maximize2,
  Minimize2,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { type ReportSummary } from "@/lib/api";

interface ReportViewerProps {
  report: ReportSummary;
  reports: ReportSummary[];
  onSelect: (report: ReportSummary) => void;
  onClose?: () => void;
  onDelete?: (id: string) => void;
  allowFullscreen?: boolean;
  /** All source names for cross-routine navigation */
  sourceNames?: string[];
  onSourceChange?: (sourceName: string) => void;
}

export function ReportViewer({
  report,
  reports,
  onSelect,
  onClose,
  onDelete,
  allowFullscreen = true,
  sourceNames,
  onSourceChange,
}: ReportViewerProps) {
  const [fullscreen, setFullscreen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const currentIndex = reports.findIndex((r) => r.id === report.id);
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < reports.length - 1;

  const goPrev = useCallback(() => {
    if (hasPrev) onSelect(reports[currentIndex - 1]);
  }, [hasPrev, currentIndex, reports, onSelect]);

  const goNext = useCallback(() => {
    if (hasNext) onSelect(reports[currentIndex + 1]);
  }, [hasNext, currentIndex, reports, onSelect]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "ArrowLeft") { goPrev(); e.preventDefault(); }
      else if (e.key === "ArrowRight") { goNext(); e.preventDefault(); }
      else if (e.key === "Escape" && fullscreen) { setFullscreen(false); e.preventDefault(); }
      else if (e.key === "f" && allowFullscreen && !e.metaKey && !e.ctrlKey) { setFullscreen((f) => !f); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goPrev, goNext, fullscreen, allowFullscreen]);

  return (
    <div
      className={`flex flex-col overflow-hidden ${
        fullscreen
          ? "fixed inset-0 z-50 rounded-none bg-[var(--color-bg)]"
          : "flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-[var(--color-text)]">
            {report.title}
          </h2>
          <div className="flex items-center gap-2 text-[10px] text-[var(--color-text-muted)]">
            <span>{new Date(report.created_at).toLocaleString()}</span>
            {report.source_name && (
              <span>{report.source_type}: {report.source_name}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {/* Source switcher */}
          {sourceNames && sourceNames.length > 1 && onSourceChange && (
            <div className="mr-2 flex items-center gap-1">
              <Layers className="h-3 w-3 text-[var(--color-text-muted)]" />
              <select
                value={report.source_name}
                onChange={(e) => onSourceChange(e.target.value)}
                className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 text-[11px] text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
              >
                {sourceNames.map((name) => (
                  <option key={name} value={name}>
                    {name.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>
          )}
          {/* Navigation counter */}
          {reports.length > 1 && (
            <span className="mr-2 text-[10px] text-[var(--color-text-muted)]">
              {currentIndex + 1} of {reports.length}
            </span>
          )}
          {/* Nav buttons */}
          <button
            onClick={goPrev}
            disabled={!hasPrev}
            className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
            title="Previous (Left Arrow)"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button
            onClick={goNext}
            disabled={!hasNext}
            className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
            title="Next (Right Arrow)"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          {allowFullscreen && (
            <button
              onClick={() => setFullscreen((f) => !f)}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title={fullscreen ? "Exit fullscreen (Esc)" : "Fullscreen (f)"}
            >
              {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </button>
          )}
          {onDelete && (
            confirmDelete ? (
              <div className="flex items-center gap-1">
                <span className="text-xs text-[var(--color-red)]">Delete?</span>
                <button
                  onClick={() => { onDelete(report.id); setConfirmDelete(false); }}
                  className="rounded px-2 py-1 text-xs font-semibold text-white bg-[var(--color-red)] hover:bg-[var(--color-red)]/80"
                >
                  Yes
                </button>
                <button
                  onClick={() => setConfirmDelete(false)}
                  className="rounded px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                >
                  No
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmDelete(true)}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)]"
                title="Delete report"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            )
          )}
          {fullscreen && onClose && (
            <button
              onClick={onClose}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {/* iframe */}
      <div className="relative flex-1">
        <iframe
          src={`/reports/${report.filename}`}
          className="h-full w-full border-0"
          title={report.title}
        />
        {/* Fullscreen chevron overlays */}
        {fullscreen && hasPrev && (
          <button
            onClick={goPrev}
            className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white/70 hover:bg-black/70 hover:text-white transition-all"
          >
            <ChevronLeft className="h-5 w-5" />
          </button>
        )}
        {fullscreen && hasNext && (
          <button
            onClick={goNext}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-black/50 p-2 text-white/70 hover:bg-black/70 hover:text-white transition-all"
          >
            <ChevronRight className="h-5 w-5" />
          </button>
        )}
      </div>
    </div>
  );
}
