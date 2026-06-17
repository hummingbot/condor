import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Loader2, RefreshCw, Search, X } from "lucide-react";
import { useState } from "react";

import { ReportViewer } from "@/components/routines/ReportViewer";
import { type ReportSummary, api } from "@/lib/api";
import { formatRelativeTime } from "@/lib/formatters";

export function Reports() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<ReportSummary | null>(null);
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["reports", search],
    queryFn: () => api.getReports({ search: search || undefined, limit: 200 }),
  });

  const reports = data?.reports ?? [];

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteReport(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ["reports"] });
      if (selected?.id === id) setSelected(null);
    },
  });

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--color-text)]">Reports</h1>
          <p className="text-xs text-[var(--color-text-muted)]">
            {data?.total ?? 0} reports
          </p>
        </div>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ["reports"] })}
          className="rounded p-2 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          title="Refresh"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      <div className="flex gap-4" style={{ minHeight: "calc(100vh - 160px)" }}>
        {/* Left: Report list */}
        <div className="w-80 shrink-0 space-y-2">
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search reports..."
              className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] py-2 pl-9 pr-8 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)] focus:outline-none"
            />
            {search && (
              <button
                onClick={() => setSearch("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
            </div>
          ) : reports.length === 0 ? (
            <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
              No reports found
            </div>
          ) : (
            reports.map((r) => {
              const isActive = r.id === selected?.id;
              return (
                <button
                  key={r.id}
                  onClick={() => setSelected(r)}
                  className={`w-full rounded-lg border p-3 text-left transition-all ${
                    isActive
                      ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5"
                      : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-primary)]/20"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-sm font-medium text-[var(--color-text)] line-clamp-2">
                      {r.title}
                    </span>
                    <span className="shrink-0 text-[10px] text-[var(--color-text-muted)]">
                      {formatRelativeTime(r.created_at)}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {r.source_type && (
                      <span className="rounded bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-[10px] font-semibold text-[var(--color-primary)]">
                        {r.source_type}
                      </span>
                    )}
                    {r.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)]"
                      >
                        #{tag}
                      </span>
                    ))}
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* Right: Report viewer */}
        {!selected ? (
          <div className="flex flex-1 flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
            <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-muted)]">
              <div className="text-center">
                <FileText className="mx-auto mb-2 h-8 w-8 opacity-30" />
                Select a report to view
              </div>
            </div>
          </div>
        ) : (
          <ReportViewer
            report={selected}
            reports={reports}
            onSelect={setSelected}
            onDelete={(id) => deleteMutation.mutate(id)}
          />
        )}
      </div>
    </div>
  );
}
