import { useQuery } from "@tanstack/react-query";
import { FileText, Loader2, Maximize2, Minimize2 } from "lucide-react";
import { useState } from "react";

import { type ReportSummary, api } from "@/lib/api";

interface RoutineReportsProps {
  routineName: string;
}

function formatAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function RoutineReports({ routineName }: RoutineReportsProps) {
  const [viewReport, setViewReport] = useState<ReportSummary | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["routine-reports", routineName],
    queryFn: () => api.getRoutineReports(routineName),
    enabled: !!routineName,
  });

  const reports = data?.reports ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-4 text-xs text-[var(--color-text-muted)]">
        <Loader2 className="h-3 w-3 animate-spin" /> Loading reports...
      </div>
    );
  }

  if (reports.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-[var(--color-border)] px-4 py-6 text-center">
        <FileText className="mx-auto mb-1.5 h-5 w-5 text-[var(--color-text-muted)]/40" />
        <p className="text-xs text-[var(--color-text-muted)]">
          No reports yet — run this routine to generate one
        </p>
      </div>
    );
  }

  return (
    <div>
      <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
        Reports ({reports.length})
      </h3>

      {/* Report cards */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        {reports.slice(0, 6).map((r) => (
          <button
            key={r.id}
            onClick={() => setViewReport(r)}
            className={`rounded-md border p-2.5 text-left transition-all ${
              viewReport?.id === r.id
                ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5"
                : "border-[var(--color-border)] hover:border-[var(--color-primary)]/20"
            }`}
          >
            <p className="text-xs font-medium text-[var(--color-text)] line-clamp-1">
              {r.title}
            </p>
            <div className="mt-1 flex items-center gap-2">
              <span className="text-[10px] text-[var(--color-text-muted)]">
                {formatAgo(r.created_at)}
              </span>
              {r.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 text-[9px] text-[var(--color-text-muted)]"
                >
                  #{tag}
                </span>
              ))}
            </div>
          </button>
        ))}
      </div>

      {/* Inline viewer */}
      {viewReport && (
        <div
          className={`overflow-hidden rounded-lg border border-[var(--color-border)] ${
            fullscreen ? "fixed inset-0 z-50 rounded-none" : ""
          }`}
        >
          <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
            <span className="text-xs font-medium text-[var(--color-text)] truncate">
              {viewReport.title}
            </span>
            <button
              onClick={() => setFullscreen((f) => !f)}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            >
              {fullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            </button>
          </div>
          <iframe
            src={`/charts/${viewReport.filename}`}
            className={`w-full border-0 ${fullscreen ? "h-[calc(100vh-40px)]" : "h-[560px]"}`}
            title={viewReport.title}
          />
        </div>
      )}
    </div>
  );
}
