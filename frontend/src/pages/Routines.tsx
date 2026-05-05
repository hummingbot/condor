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
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { CategoryPills } from "@/components/routines/CategoryPills";
import { RoutineCatalog } from "@/components/routines/RoutineCatalog";
import { RoutineDetail } from "@/components/routines/RoutineDetail";
import { useServer } from "@/hooks/useServer";
import { type ReportSummary, type RoutineInfo, api } from "@/lib/api";

type StatusFilter = "all" | "running" | "scheduled";

export function Routines() {
  const { server } = useServer();
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") === "reports" ? "reports" : "routines";

  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("All");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  // ── Queries ──

  const { data: routines = [], isLoading: loadingRoutines } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
  });

  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    refetchInterval: 5000,
  });

  // ── Filtering ──

  const filteredRoutines = useMemo(() => {
    let result = routines;

    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          r.description.toLowerCase().includes(q) ||
          r.category.toLowerCase().includes(q),
      );
    }

    if (categoryFilter !== "All") {
      result = result.filter((r) => r.category === categoryFilter);
    }

    if (statusFilter !== "all") {
      const activeNames = new Set(
        instances
          .filter((i) => i.status === statusFilter || (statusFilter === "running" && i.status === "running"))
          .map((i) => i.routine_name),
      );
      result = result.filter((r) => activeNames.has(r.name));
    }

    return result;
  }, [routines, search, categoryFilter, statusFilter, instances]);

  const selectedRoutine = useMemo(
    () => routines.find((r) => r.name === selected),
    [routines, selected],
  );

  const handleSelect = useCallback((routine: RoutineInfo) => {
    setSelected(routine.name);
  }, []);

  // ── Stats ──

  const runningCount = instances.filter((i) => i.status === "running").length;
  const scheduledCount = instances.filter((i) => i.status === "scheduled").length;

  if (!server) {
    return (
      <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
        Select a server to view routines
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          <button
            onClick={() => setSearchParams({})}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              activeTab === "routines"
                ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            Routines
          </button>
          <button
            onClick={() => setSearchParams({ tab: "reports" })}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              activeTab === "reports"
                ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            All Reports
          </button>
        </div>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: activeTab === "reports" ? ["reports"] : ["routines"] })}
          className="rounded p-2 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          title="Refresh"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      {activeTab === "routines" ? (
        <RoutinesTab
          routines={routines}
          filteredRoutines={filteredRoutines}
          instances={instances}
          selectedRoutine={selectedRoutine}
          selected={selected}
          onSelect={handleSelect}
          search={search}
          onSearchChange={setSearch}
          categoryFilter={categoryFilter}
          onCategoryChange={setCategoryFilter}
          statusFilter={statusFilter}
          onStatusChange={setStatusFilter}
          loadingRoutines={loadingRoutines}
          runningCount={runningCount}
          scheduledCount={scheduledCount}
        />
      ) : (
        <AllReportsTab />
      )}
    </div>
  );
}

// ─��� Routines Tab ──

interface RoutinesTabProps {
  routines: RoutineInfo[];
  filteredRoutines: RoutineInfo[];
  instances: ReturnType<typeof api.getRoutineInstances> extends Promise<infer T> ? T : never;
  selectedRoutine: RoutineInfo | undefined;
  selected: string | null;
  onSelect: (r: RoutineInfo) => void;
  search: string;
  onSearchChange: (s: string) => void;
  categoryFilter: string;
  onCategoryChange: (c: string) => void;
  statusFilter: StatusFilter;
  onStatusChange: (s: StatusFilter) => void;
  loadingRoutines: boolean;
  runningCount: number;
  scheduledCount: number;
}

function RoutinesTab({
  routines,
  filteredRoutines,
  instances,
  selectedRoutine,
  selected,
  onSelect,
  search,
  onSearchChange,
  categoryFilter,
  onCategoryChange,
  statusFilter,
  onStatusChange,
  loadingRoutines,
  runningCount,
  scheduledCount,
}: RoutinesTabProps) {
  return (
    <>
      {/* Header stats */}
      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
        <span>{routines.length} available</span>
        <span className="h-3 w-px bg-[var(--color-border)]" />
        {runningCount > 0 && (
          <span className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            {runningCount} running
          </span>
        )}
        {scheduledCount > 0 && (
          <span className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
            {scheduledCount} scheduled
          </span>
        )}
      </div>

      {/* Search */}
      <div className="relative max-w-xs">
        <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
        <input
          type="text"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search routines..."
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] py-1.5 pl-9 pr-8 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)] focus:outline-none"
        />
        {search && (
          <button
            onClick={() => onSearchChange("")}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Category + Status pills */}
      <div className="space-y-2">
        <CategoryPills
          routines={routines}
          activeCategory={categoryFilter}
          onSelect={onCategoryChange}
        />
        <div className="flex gap-1.5">
          {(["all", "running", "scheduled"] as const).map((s) => (
            <button
              key={s}
              onClick={() => onStatusChange(s)}
              className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-all ${
                statusFilter === s
                  ? "bg-[var(--color-text)] text-[var(--color-bg)]"
                  : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {s === "all" ? "All Status" : s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Two-column layout */}
      <div className="flex gap-4" style={{ minHeight: "calc(100vh - 280px)" }}>
        {/* Left: Catalog */}
        <div className="w-80 shrink-0 overflow-y-auto pr-1" style={{ maxHeight: "calc(100vh - 280px)" }}>
          {loadingRoutines ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
            </div>
          ) : filteredRoutines.length === 0 ? (
            <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
              No routines match filters
            </div>
          ) : (
            <RoutineCatalog
              routines={filteredRoutines}
              instances={instances}
              selected={selected}
              onSelect={onSelect}
            />
          )}
        </div>

        {/* Right: Detail */}
        <div className="flex-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5" style={{ maxHeight: "calc(100vh - 280px)" }}>
          {!selectedRoutine ? (
            <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-muted)]">
              Select a routine from the catalog
            </div>
          ) : (
            <RoutineDetail routine={selectedRoutine} instances={instances} />
          )}
        </div>
      </div>
    </>
  );
}

// ── All Reports Tab (ported from Reports.tsx) ──

function AllReportsTab() {
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
    <div className="flex gap-4" style={{ minHeight: "calc(100vh - 160px)" }}>
      {/* Left: Report list */}
      <div className="w-80 shrink-0 space-y-2">
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

        <p className="text-xs text-[var(--color-text-muted)]">{data?.total ?? 0} reports</p>

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
                    {formatReportAgo(r.created_at)}
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
      <div
        className={`flex flex-col overflow-hidden ${
          fullscreen
            ? "fixed inset-0 z-50 rounded-none bg-[var(--color-bg)]"
            : "flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
        }`}
      >
        {!selected ? (
          <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-muted)]">
            <div className="text-center">
              <FileText className="mx-auto mb-2 h-8 w-8 opacity-30" />
              Select a report to view
            </div>
          </div>
        ) : (
          <>
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
            <iframe
              src={`/charts/${selected.filename}`}
              className="flex-1 w-full border-0"
              title={selected.title}
            />
          </>
        )}
      </div>
    </div>
  );
}

function formatReportAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
