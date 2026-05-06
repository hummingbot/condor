import { useQuery } from "@tanstack/react-query";
import { ExternalLink, FileText, Loader2 } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ReportViewer } from "@/components/routines/ReportViewer";
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
  const [, setSearchParams] = useSearchParams();

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
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
          Reports ({reports.length})
        </h3>
        {reports.length > 12 && (
          <button
            onClick={() => setSearchParams({ tab: "reports", source: routineName })}
            className="flex items-center gap-1 text-[10px] text-[var(--color-primary)] hover:underline"
          >
            Show all {reports.length} <ExternalLink className="h-3 w-3" />
          </button>
        )}
      </div>

      {/* Report cards */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        {reports.slice(0, 12).map((r) => (
          <button
            key={r.id}
            onClick={() => setViewReport(r)}
            className={`group rounded-md border p-2.5 text-left transition-all ${
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

      {reports.length > 12 && !viewReport && (
        <button
          onClick={() => setSearchParams({ tab: "reports", source: routineName })}
          className="mb-3 text-[11px] text-[var(--color-primary)] hover:underline"
        >
          Show all {reports.length} reports
        </button>
      )}

      {/* Inline viewer */}
      {viewReport && (
        <div className="h-[560px]">
          <ReportViewer
            report={viewReport}
            reports={reports}
            onSelect={setViewReport}
            onClose={() => setViewReport(null)}
            allowFullscreen={true}
          />
        </div>
      )}
    </div>
  );
}
