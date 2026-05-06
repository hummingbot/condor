import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  Play,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { CategoryPills } from "@/components/routines/CategoryPills";
import { ReportViewer } from "@/components/routines/ReportViewer";
import { RoutineCatalog } from "@/components/routines/RoutineCatalog";
import { RoutineDetail } from "@/components/routines/RoutineDetail";
import { useServer } from "@/hooks/useServer";
import { type ReportGroup, type ReportSummary, type RoutineInfo, api } from "@/lib/api";

type TabKey = "dashboard" | "reports" | "catalog";
type StatusFilter = "all" | "running" | "scheduled";

export function Routines() {
  const { server } = useServer();
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const rawTab = searchParams.get("tab");
  const activeTab: TabKey =
    rawTab === "reports" ? "reports" : rawTab === "catalog" ? "catalog" : "dashboard";

  const setTab = useCallback(
    (tab: TabKey, extra?: Record<string, string>) => {
      const params: Record<string, string> = tab === "dashboard" ? {} : { tab };
      if (extra) Object.assign(params, extra);
      setSearchParams(params);
    },
    [setSearchParams],
  );

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
          {(["dashboard", "reports", "catalog"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setTab(tab)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                activeTab === tab
                  ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {tab === "dashboard" ? "Dashboard" : tab === "reports" ? "Reports" : "Catalog"}
            </button>
          ))}
        </div>
        <button
          onClick={() => {
            qc.invalidateQueries({ queryKey: ["routines"] });
            qc.invalidateQueries({ queryKey: ["routine-instances"] });
            qc.invalidateQueries({ queryKey: ["reports"] });
            qc.invalidateQueries({ queryKey: ["reports-grouped"] });
          }}
          className="rounded p-2 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          title="Refresh"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </div>

      {activeTab === "dashboard" && <DashboardTab onNavigate={setTab} />}
      {activeTab === "reports" && <ReportsTab sourceFilter={searchParams.get("source") ?? undefined} />}
      {activeTab === "catalog" && <CatalogTab />}
    </div>
  );
}

// ── Dashboard Tab ──

function DashboardTab({ onNavigate }: { onNavigate: (tab: TabKey, extra?: Record<string, string>) => void }) {
  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    refetchInterval: 5000,
  });

  const { data: groups = [], isLoading: loadingGroups } = useQuery({
    queryKey: ["reports-grouped"],
    queryFn: api.getReportsGrouped,
    refetchInterval: 30000,
  });

  const activeInstances = instances.filter(
    (i) => i.status === "running" || i.status === "scheduled",
  );

  return (
    <div className="space-y-6">
      {/* Section A: Active Routines Strip */}
      <div>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
          Active Routines
        </h2>
        {activeInstances.length === 0 ? (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] px-6 py-8 text-center">
            <Play className="mx-auto mb-2 h-5 w-5 text-[var(--color-text-muted)]/40" />
            <p className="text-sm text-[var(--color-text-muted)]">
              No active routines
            </p>
            <button
              onClick={() => onNavigate("catalog")}
              className="mt-2 text-xs text-[var(--color-primary)] hover:underline"
            >
              Go to Catalog to run one
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap gap-3">
            {activeInstances.map((inst) => (
              <button
                key={inst.instance_id}
                onClick={() => onNavigate("catalog")}
                className="group relative rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-left transition-all hover:border-[var(--color-primary)]/30"
                style={{
                  borderLeftWidth: "3px",
                  borderLeftColor: inst.status === "running" ? "var(--color-green, #3fb950)" : "var(--color-warning, #d29922)",
                }}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`h-2 w-2 rounded-full ${
                      inst.status === "running"
                        ? "bg-emerald-400 animate-pulse"
                        : "bg-amber-400"
                    }`}
                  />
                  <span className="text-sm font-medium text-[var(--color-text)]">
                    {inst.routine_name}
                  </span>
                </div>
                <div className="mt-1.5 flex items-center gap-3 text-[10px] text-[var(--color-text-muted)]">
                  <span className="capitalize">{inst.status}</span>
                  {inst.run_count > 0 && <span>{inst.run_count} runs</span>}
                  {inst.last_run_at && (
                    <span>{formatAgo(new Date(inst.last_run_at * 1000).toISOString())}</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Section B: Latest Reports by Type */}
      <div>
        {loadingGroups ? (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
          </div>
        ) : groups.length === 0 ? (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] px-6 py-8 text-center">
            <FileText className="mx-auto mb-2 h-5 w-5 text-[var(--color-text-muted)]/40" />
            <p className="text-sm text-[var(--color-text-muted)]">
              No reports generated yet
            </p>
          </div>
        ) : (
          <GroupedReportCards groups={groups} onNavigate={onNavigate} />
        )}
      </div>
    </div>
  );
}

// ── Grouped Report Cards (Dashboard section B) ──

function GroupedReportCards({
  groups,
  onNavigate,
}: {
  groups: ReportGroup[];
  onNavigate: (tab: TabKey, extra?: Record<string, string>) => void;
}) {
  // Group by source_type
  const byType = useMemo(() => {
    const map = new Map<string, ReportGroup[]>();
    for (const g of groups) {
      const type = g.source_type || "other";
      if (!map.has(type)) map.set(type, []);
      map.get(type)!.push(g);
    }
    return Array.from(map.entries());
  }, [groups]);

  const typeLabels: Record<string, string> = {
    routine: "Routines",
    agent: "Trading Agents",
    other: "Other",
  };

  const showSectionHeaders = byType.length > 1;

  return (
    <div className="space-y-5">
      {byType.map(([sourceType, typeGroups]) => (
        <div key={sourceType}>
          {showSectionHeaders && (
            <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              {typeLabels[sourceType] ?? sourceType}
            </h2>
          )}
          {!showSectionHeaders && (
            <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Latest Reports by Type
            </h2>
          )}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {typeGroups.map((g) => (
              <button
                key={g.source_name}
                onClick={() => onNavigate("reports", { source: g.source_name })}
                className="group rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-left transition-all hover:border-[var(--color-primary)]/30 hover:scale-[1.01]"
              >
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-[var(--color-text)] truncate">
                      {g.source_name.replace(/_/g, " ")}
                    </p>
                    {showSectionHeaders ? null : g.source_type ? (
                      <span className="mt-1 inline-block rounded bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-[10px] font-semibold text-[var(--color-primary)]">
                        {g.source_type}
                      </span>
                    ) : null}
                  </div>
                  <span className="shrink-0 rounded-full bg-[var(--color-surface-hover)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)]">
                    {g.total_count} {g.total_count === 1 ? "report" : "reports"}
                  </span>
                </div>
                <p className="mt-2 text-xs text-[var(--color-text-muted)] truncate">
                  {g.latest_report.title}
                </p>
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-[10px] text-[var(--color-text-muted)]">
                    {formatAgo(g.latest_report.created_at)}
                  </span>
                  {g.all_tags.slice(0, 3).map((tag) => (
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
        </div>
      ))}
    </div>
  );
}

// ── Reports Tab ──

function ReportsTab({ sourceFilter }: { sourceFilter?: string }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<ReportSummary | null>(null);
  const [search, setSearch] = useState("");
  const [viewMode, setViewMode] = useState<"grouped" | "all">(sourceFilter ? "grouped" : "grouped");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    new Set(sourceFilter ? [sourceFilter] : []),
  );
  const [activeSourceFilter, setActiveSourceFilter] = useState<string | undefined>(sourceFilter);

  const { data, isLoading } = useQuery({
    queryKey: ["reports", search, activeSourceFilter],
    queryFn: () =>
      api.getReports({
        search: search || undefined,
        limit: 200,
        source_type: undefined,
      }),
  });

  const allReports = data?.reports ?? [];

  // All unique source names (for cross-routine navigation)
  const allSourceNames = useMemo(() => {
    const names = new Set<string>();
    for (const r of allReports) {
      if (r.source_name) names.add(r.source_name);
    }
    return Array.from(names).sort();
  }, [allReports]);

  const reports = useMemo(() => {
    if (activeSourceFilter) {
      return allReports.filter((r) => r.source_name === activeSourceFilter);
    }
    return allReports;
  }, [allReports, activeSourceFilter]);

  // Group reports by source_name
  const grouped = useMemo(() => {
    const map = new Map<string, ReportSummary[]>();
    for (const r of reports) {
      const key = r.source_name || "Other";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(r);
    }
    return Array.from(map.entries()).sort(
      (a, b) => new Date(b[1][0].created_at).getTime() - new Date(a[1][0].created_at).getTime(),
    );
  }, [reports]);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteReport(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ["reports"] });
      if (selected?.id === id) setSelected(null);
    },
  });

  const toggleGroup = (name: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  // Auto-select latest report when source filter is applied
  useMemo(() => {
    if (sourceFilter && reports.length > 0 && !selected) {
      setSelected(reports[0]);
    }
  }, [sourceFilter, reports, selected]);

  return (
    <div className="flex gap-4" style={{ minHeight: "calc(100vh - 160px)" }}>
      {/* Left sidebar */}
      <div className="w-80 shrink-0 space-y-2 overflow-y-auto" style={{ maxHeight: "calc(100vh - 160px)" }}>
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

        {/* View toggle + count */}
        <div className="flex items-center justify-between">
          <p className="text-xs text-[var(--color-text-muted)]">{reports.length} reports</p>
          <div className="flex gap-1 rounded-md border border-[var(--color-border)] p-0.5">
            <button
              onClick={() => setViewMode("grouped")}
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                viewMode === "grouped"
                  ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              Grouped
            </button>
            <button
              onClick={() => setViewMode("all")}
              className={`rounded px-2 py-0.5 text-[10px] font-medium transition-colors ${
                viewMode === "all"
                  ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              All
            </button>
          </div>
        </div>

        {/* Source filter pills */}
        {allSourceNames.length > 1 && (
          <div className="flex flex-wrap gap-1">
            <button
              onClick={() => setActiveSourceFilter(undefined)}
              className={`rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
                !activeSourceFilter
                  ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                  : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              All
            </button>
            {allSourceNames.map((name) => (
              <button
                key={name}
                onClick={() => setActiveSourceFilter(name === activeSourceFilter ? undefined : name)}
                className={`rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
                  activeSourceFilter === name
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                {name.replace(/_/g, " ")}
              </button>
            ))}
          </div>
        )}

        {isLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
          </div>
        ) : reports.length === 0 ? (
          <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
            No reports found
          </div>
        ) : viewMode === "grouped" ? (
          /* Grouped view */
          <div className="space-y-1">
            {grouped.map(([sourceName, groupReports]) => {
              const isExpanded = expandedGroups.has(sourceName);
              return (
                <div key={sourceName}>
                  <button
                    onClick={() => toggleGroup(sourceName)}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-[var(--color-surface-hover)] transition-colors"
                  >
                    {isExpanded ? (
                      <ChevronDown className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
                    ) : (
                      <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
                    )}
                    <span className="flex-1 truncate text-xs font-medium text-[var(--color-text)]">
                      {sourceName}
                    </span>
                    <span className="text-[10px] text-[var(--color-text-muted)]">
                      {formatAgo(groupReports[0].created_at)}
                    </span>
                    <span className="rounded-full bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[9px] text-[var(--color-text-muted)]">
                      {groupReports.length}
                    </span>
                  </button>
                  {isExpanded && (
                    <div className="ml-5 space-y-1 py-1">
                      {groupReports.slice(0, 5).map((r) => (
                        <ReportListItem
                          key={r.id}
                          report={r}
                          isActive={r.id === selected?.id}
                          onSelect={() => setSelected(r)}
                        />
                      ))}
                      {groupReports.length > 5 && (
                        <button
                          onClick={() => {
                            setActiveSourceFilter(sourceName);
                            setViewMode("all");
                          }}
                          className="pl-2 text-[10px] text-[var(--color-primary)] hover:underline"
                        >
                          Show all {groupReports.length} reports
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          /* All (flat) view */
          <div className="space-y-1">
            {reports.map((r) => (
              <ReportListItem
                key={r.id}
                report={r}
                isActive={r.id === selected?.id}
                onSelect={() => setSelected(r)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Right: Report viewer */}
      {!selected ? (
        <div className="flex flex-1 items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
          <div className="text-center text-sm text-[var(--color-text-muted)]">
            <FileText className="mx-auto mb-2 h-8 w-8 opacity-30" />
            Select a report to view
          </div>
        </div>
      ) : (
        <ReportViewer
          report={selected}
          reports={reports}
          onSelect={setSelected}
          onDelete={(id) => deleteMutation.mutate(id)}
          sourceNames={allSourceNames}
          onSourceChange={(name) => {
            setActiveSourceFilter(name);
            // Select latest report from that source
            const sourceReports = allReports.filter((r) => r.source_name === name);
            if (sourceReports.length > 0) setSelected(sourceReports[0]);
          }}
        />
      )}
    </div>
  );
}

function ReportListItem({
  report,
  isActive,
  onSelect,
}: {
  report: ReportSummary;
  isActive: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-md border p-2.5 text-left transition-all ${
        isActive
          ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5"
          : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-primary)]/20"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium text-[var(--color-text)] line-clamp-2">
          {report.title}
        </span>
        <span className="shrink-0 text-[10px] text-[var(--color-text-muted)]">
          {formatAgo(report.created_at)}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {report.source_type && (
          <span className="rounded bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-[10px] font-semibold text-[var(--color-primary)]">
            {report.source_type}
          </span>
        )}
        {report.tags.slice(0, 2).map((tag) => (
          <span
            key={tag}
            className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 text-[9px] text-[var(--color-text-muted)]"
          >
            #{tag}
          </span>
        ))}
      </div>
    </button>
  );
}

// ── Catalog Tab ──

function CatalogTab() {
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("All");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const { data: routines = [], isLoading: loadingRoutines } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
  });

  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    refetchInterval: 5000,
  });

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

  const runningCount = instances.filter((i) => i.status === "running").length;
  const scheduledCount = instances.filter((i) => i.status === "scheduled").length;

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
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search routines..."
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] py-1.5 pl-9 pr-8 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)] focus:outline-none"
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

      {/* Category + Status pills */}
      <div className="space-y-2">
        <CategoryPills
          routines={routines}
          activeCategory={categoryFilter}
          onSelect={setCategoryFilter}
        />
        <div className="flex gap-1.5">
          {(["all", "running", "scheduled"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
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
              onSelect={handleSelect}
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

// ── Utilities ──

function formatAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
