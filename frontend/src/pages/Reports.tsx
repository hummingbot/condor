import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileText,
  Loader2,
  Maximize2,
  Minimize2,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";

import { type ReportSummary, api } from "@/lib/api";

function formatAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function Reports() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<ReportSummary | null>(null);
  const [search, setSearch] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

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
      setConfirmDelete(null);
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
                      {formatAgo(r.created_at)}
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
        <div className={`flex flex-col overflow-hidden ${
          fullscreen
            ? "fixed inset-0 z-50 rounded-none bg-[var(--color-bg)]"
            : "flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
        }`}>
          {!selected ? (
            <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-muted)]">
              <div className="text-center">
                <FileText className="mx-auto mb-2 h-8 w-8 opacity-30" />
                Select a report to view
              </div>
            </div>
          ) : (
            <>
              {/* Report header */}
              <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-semibold text-[var(--color-text)]">
                    {selected.title}
                  </h2>
                  <div className="flex items-center gap-2 text-[10px] text-[var(--color-text-muted)]">
                    <span>{new Date(selected.created_at).toLocaleString()}</span>
                    {selected.source_name && (
                      <span>
                        {selected.source_type}: {selected.source_name}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setFullscreen((f) => !f)}
                    className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                    title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
                  >
                    {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
                  </button>
                  {confirmDelete === selected.id ? (
                    <div className="flex items-center gap-1">
                      <span className="text-xs text-[var(--color-red)]">Delete?</span>
                      <button
                        onClick={() => deleteMutation.mutate(selected.id)}
                        disabled={deleteMutation.isPending}
                        className="rounded px-2 py-1 text-xs font-semibold text-white bg-[var(--color-red)] hover:bg-[var(--color-red)]/80"
                      >
                        Yes
                      </button>
                      <button
                        onClick={() => setConfirmDelete(null)}
                        className="rounded px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                      >
                        No
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmDelete(selected.id)}
                      className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)]"
                      title="Delete report"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>

              {/* Report iframe */}
              <iframe
                src={`/charts/${selected.filename}`}
                className="flex-1 w-full border-0"
                title={selected.title}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
