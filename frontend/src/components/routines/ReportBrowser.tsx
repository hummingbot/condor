import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock,
  Loader2,
  MessageSquare,
  Moon,
  Play,
  PlayCircle,
  Settings2,
  Square,
  Sun,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type RoutineInstance, api } from "@/lib/api";
import { setViewContext } from "@/lib/viewContext";
import { useServer } from "@/hooks/useServer";
import { RoutineConfigForm } from "./RoutineConfigForm";
import { ScheduleDropdown } from "./ScheduleDropdown";

interface ReportBrowserProps {
  initialSource?: string;
  initialSourceTypeFilter?: string;
  instances: RoutineInstance[];
  onClose: () => void;
}

export function ReportBrowser({
  instances,
  initialSource,
  initialSourceTypeFilter,
  onClose,
}: ReportBrowserProps) {
  const { server } = useServer();
  const qc = useQueryClient();
  const [sourceTypeFilter, setSourceTypeFilter] = useState<string>(
    initialSourceTypeFilter || "all",
  );
  const [isCompact, setIsCompact] = useState(false);
  const [showConfigPanel, setShowConfigPanel] = useState(false);
  const [reportTheme, setReportTheme] = useState<"dark" | "light">("dark");
  const sidebarRef = useRef<HTMLDivElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  const timelineContentRef = useRef<HTMLDivElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [canScrollTimelineLeft, setCanScrollTimelineLeft] = useState(false);
  const [canScrollTimelineRight, setCanScrollTimelineRight] = useState(false);
  const [timelineOverflow, setTimelineOverflow] = useState(false);

  // Fetch all routines for the sidebar
  const { data: routines = [] } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
  });

  const [activeSource, setActiveSource] = useState(initialSource ?? "");

  // Filter routines by source type
  const filteredRoutines = useMemo(() => {
    if (sourceTypeFilter === "all") return routines;
    if (sourceTypeFilter === "routine")
      return routines.filter((r) => !r.source.startsWith("agent:"));
    if (sourceTypeFilter === "agent") return routines.filter((r) => r.source.startsWith("agent:"));
    // Specific agent name
    return routines.filter((r) => r.source === `agent:${sourceTypeFilter}`);
  }, [routines, sourceTypeFilter]);

  const effectiveActiveSource = activeSource || filteredRoutines[0]?.name || "";
  const effectiveActiveSourceRef = useRef(effectiveActiveSource);
  useEffect(() => {
    effectiveActiveSourceRef.current = effectiveActiveSource;
  }, [effectiveActiveSource]);

  // Unique source types for filter
  const hasAgents = routines.some((r) => r.source.startsWith("agent:"));

  // Agent names for sub-filter
  const agentNames = useMemo(() => {
    const names = new Set<string>();
    for (const r of routines) {
      if (r.source.startsWith("agent:")) {
        names.add(r.source.replace("agent:", ""));
      }
    }
    return Array.from(names).sort();
  }, [routines]);

  // Active routine info
  const activeRoutine = useMemo(
    () => routines.find((r) => r.name === effectiveActiveSource),
    [routines, effectiveActiveSource],
  );
  const isAgent = activeRoutine?.source.startsWith("agent:") ?? false;

  // Reports for active source
  const { data: reportsData, isLoading: loadingReports } = useQuery({
    queryKey: ["routine-reports", effectiveActiveSource],
    queryFn: () => api.getRoutineReports(effectiveActiveSource),
    enabled: !!effectiveActiveSource,
  });
  const reports = useMemo(() => reportsData?.reports ?? [], [reportsData?.reports]);

  const [selectedReportIdx, setSelectedReportIdx] = useState(0);
  const prevSourceForReportRef = useRef(effectiveActiveSource);
  if (prevSourceForReportRef.current !== effectiveActiveSource) {
    prevSourceForReportRef.current = effectiveActiveSource;
    setSelectedReportIdx(0);
  }
  const selectedReport = reports[selectedReportIdx] ?? null;

  // Active instances for current source
  const sourceInstances = useMemo(
    () =>
      instances.filter(
        (i) =>
          i.routine_name === effectiveActiveSource &&
          (i.status === "running" || i.status === "scheduled"),
      ),
    [instances, effectiveActiveSource],
  );

  // Config state: prefer last-used config from instances, then defaults from routine fields
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const configSourceRef = useRef<string | null>(null);
  if (configSourceRef.current !== effectiveActiveSource) {
    configSourceRef.current = effectiveActiveSource;
    const lastInstance = instances.find((i) => i.routine_name === effectiveActiveSource);
    if (lastInstance && Object.keys(lastInstance.config || {}).length > 0) {
      setConfigValues({ ...lastInstance.config });
    } else if (activeRoutine) {
      const defaults: Record<string, unknown> = {};
      for (const [key, field] of Object.entries(activeRoutine.fields)) {
        defaults[key] = field.default;
      }
      setConfigValues(defaults);
    } else {
      setConfigValues({});
    }
    setShowConfigPanel(false);
  }

  // Track running instance to poll for completion
  const [pollingInstanceId, setPollingInstanceId] = useState<string | null>(null);

  useQuery({
    queryKey: ["routine-instance", pollingInstanceId],
    queryFn: () => api.getRoutineInstance(pollingInstanceId!),
    enabled: !!pollingInstanceId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && status !== "running") {
        queueMicrotask(() => {
          setPollingInstanceId(null);
          const source = effectiveActiveSourceRef.current;
          qc.invalidateQueries({ queryKey: ["routine-reports", source] });
          qc.invalidateQueries({ queryKey: ["reports-grouped"] });
          qc.invalidateQueries({ queryKey: ["routines"] });
          qc.invalidateQueries({ queryKey: ["routine-instances"] });
        });
        return false;
      }
      return 2000;
    },
  });

  const runMutation = useMutation({
    mutationFn: () => api.runRoutine(server!, effectiveActiveSource, configValues),
    onSuccess: (data) => {
      setPollingInstanceId(data.instance_id);
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
      setShowConfigPanel(false);
    },
  });

  const scheduleMutation = useMutation({
    mutationFn: (intervalSec: number) =>
      api.scheduleRoutine(server!, effectiveActiveSource, configValues, intervalSec),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
      setShowConfigPanel(false);
    },
  });

  const stopMutation = useMutation({
    mutationFn: (id: string) => api.stopRoutineInstance(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteReport(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ["reports"] });
      qc.invalidateQueries({ queryKey: ["reports-grouped"] });
      qc.invalidateQueries({
        queryKey: ["routine-reports", effectiveActiveSource],
      });
      if (selectedReport?.id === id) {
        setSelectedReportIdx(Math.max(0, selectedReportIdx - 1));
      }
    },
  });

  // Run All state
  const [runAllProgress, setRunAllProgress] = useState<{
    current: number;
    total: number;
  } | null>(null);

  const runAll = useCallback(async () => {
    if (!server || filteredRoutines.length === 0) return;
    const toRun = filteredRoutines;
    setRunAllProgress({ current: 0, total: toRun.length });
    for (let i = 0; i < toRun.length; i++) {
      setRunAllProgress({ current: i + 1, total: toRun.length });
      const routine = toRun[i];
      const defaults: Record<string, unknown> = {};
      for (const [key, field] of Object.entries(routine.fields)) {
        defaults[key] = field.default;
      }
      try {
        await api.runRoutine(server, routine.name, defaults);
      } catch {
        // continue with remaining routines
      }
    }
    setRunAllProgress(null);
    qc.invalidateQueries({ queryKey: ["routine-reports"] });
    qc.invalidateQueries({ queryKey: ["reports-grouped"] });
    qc.invalidateQueries({ queryKey: ["routines"] });
    qc.invalidateQueries({ queryKey: ["routine-instances"] });
  }, [server, filteredRoutines, qc]);

  // Sync theme to iframe when it changes or report changes
  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    const sendTheme = () => {
      iframe.contentWindow?.postMessage({ type: "set-theme", theme: reportTheme }, "*");
    };
    iframe.addEventListener("load", sendTheme);
    sendTheme();
    return () => iframe.removeEventListener("load", sendTheme);
  }, [reportTheme, selectedReport]);

  const [confirmDelete, setConfirmDelete] = useState(false);

  // Keyboard navigation
  const activeSourceIdx = filteredRoutines.findIndex((r) => r.name === effectiveActiveSource);

  const goSourceUp = useCallback(() => {
    if (activeSourceIdx > 0) {
      setActiveSource(filteredRoutines[activeSourceIdx - 1].name);
    }
  }, [activeSourceIdx, filteredRoutines]);

  const goSourceDown = useCallback(() => {
    if (activeSourceIdx < filteredRoutines.length - 1) {
      setActiveSource(filteredRoutines[activeSourceIdx + 1].name);
    }
  }, [activeSourceIdx, filteredRoutines]);

  const goPrevReport = useCallback(() => {
    if (selectedReportIdx > 0) setSelectedReportIdx((i) => i - 1);
  }, [selectedReportIdx]);

  const goNextReport = useCallback(() => {
    if (selectedReportIdx < reports.length - 1) setSelectedReportIdx((i) => i + 1);
  }, [selectedReportIdx, reports.length]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      )
        return;
      if (e.key === "ArrowUp") {
        goSourceUp();
        e.preventDefault();
      } else if (e.key === "ArrowDown") {
        goSourceDown();
        e.preventDefault();
      } else if (e.key === "ArrowLeft") {
        goPrevReport();
        e.preventDefault();
      } else if (e.key === "ArrowRight") {
        goNextReport();
        e.preventDefault();
      } else if (e.key === "Escape") {
        if (showConfigPanel) setShowConfigPanel(false);
        else onClose();
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goSourceUp, goSourceDown, goPrevReport, goNextReport, onClose, showConfigPanel]);

  const scrollTimeline = useCallback((direction: "left" | "right") => {
    const el = timelineRef.current;
    if (!el) return;
    el.scrollBy({
      left: direction === "left" ? -el.clientWidth * 0.6 : el.clientWidth * 0.6,
      behavior: "smooth",
    });
  }, []);

  // Scroll active source into view
  useEffect(() => {
    const el = sidebarRef.current?.querySelector("[data-active-source]");
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [effectiveActiveSource]);

  // Track timeline overflow for scroll arrows (setState only in event/callback handlers)
  useEffect(() => {
    const el = timelineRef.current;
    const content = timelineContentRef.current;
    if (!el) return;

    const update = () => {
      const overflow = el.scrollWidth > el.clientWidth + 1;
      setTimelineOverflow(overflow);
      setCanScrollTimelineLeft(el.scrollLeft > 0);
      setCanScrollTimelineRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
    };

    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    if (content) ro.observe(content);

    const raf = requestAnimationFrame(update);

    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [reports.length]);

  // Scroll active report tab into view
  useEffect(() => {
    const el = timelineRef.current?.querySelector("[data-active-report]");
    el?.scrollIntoView({
      block: "nearest",
      inline: "nearest",
      behavior: "smooth",
    });
  }, [selectedReportIdx]);

  // Update view context for chat integration
  useEffect(() => {
    if (selectedReport) {
      setViewContext({
        filename: selectedReport.filename,
        title: selectedReport.title,
        source_name: effectiveActiveSource,
      });
    }
    return () => setViewContext(null);
  }, [selectedReport, effectiveActiveSource]);

  return (
    <div className="fixed inset-0 z-50 flex overflow-hidden bg-[var(--color-bg)]">
      {/* Left sidebar: routine list */}
      <div
        className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
          isCompact ? "w-12" : "w-64"
        }`}
      >
        {/* Sidebar header */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2.5">
          {!isCompact && (
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Routines
            </span>
          )}
          <button
            onClick={() => setIsCompact(!isCompact)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            {isCompact ? (
              <ChevronRight className="h-3.5 w-3.5" />
            ) : (
              <ChevronLeft className="h-3.5 w-3.5" />
            )}
          </button>
        </div>

        {/* Source type filter */}
        {!isCompact && hasAgents && (
          <div className="flex flex-wrap gap-1 border-b border-[var(--color-border)] px-3 py-2">
            <button
              onClick={() => setSourceTypeFilter("all")}
              className={`rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
                sourceTypeFilter === "all"
                  ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              All
            </button>
            <button
              onClick={() => setSourceTypeFilter("routine")}
              className={`rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
                sourceTypeFilter === "routine"
                  ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              Routines
            </button>
            <button
              onClick={() => setSourceTypeFilter("agent")}
              className={`flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
                sourceTypeFilter === "agent"
                  ? "bg-purple-500/10 text-purple-400"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              <Brain className="h-2.5 w-2.5" />
              Agents
            </button>
            {agentNames.map((name) => (
              <button
                key={name}
                onClick={() => setSourceTypeFilter(name)}
                className={`flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
                  sourceTypeFilter === name
                    ? "bg-purple-500/10 text-purple-400"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                <Brain className="h-2 w-2" />
                {name}
              </button>
            ))}
          </div>
        )}

        {/* Routine list */}
        <div ref={sidebarRef} className="flex-1 overflow-y-auto scrollbar-thin">
          {filteredRoutines.map((r) => {
            const isActive = r.name === effectiveActiveSource;
            const hasActiveInstance = instances.some(
              (i) =>
                i.routine_name === r.name && (i.status === "running" || i.status === "scheduled"),
            );
            const isRoutineAgent = r.source.startsWith("agent:");
            const displayName = r.name.replace(/_/g, " ");

            if (isCompact) {
              return (
                <button
                  key={r.name}
                  onClick={() => setActiveSource(r.name)}
                  {...(isActive ? { "data-active-source": true } : {})}
                  className={`flex w-full items-center justify-center py-3 transition-colors ${
                    isActive
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title={displayName}
                >
                  {isRoutineAgent ? (
                    <Brain className="h-4 w-4 text-purple-400" />
                  ) : hasActiveInstance ? (
                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]" />
                  ) : (
                    <span className="text-[10px] font-bold uppercase leading-none">
                      {displayName.slice(0, 2)}
                    </span>
                  )}
                </button>
              );
            }

            return (
              <button
                key={r.name}
                onClick={() => setActiveSource(r.name)}
                {...(isActive ? { "data-active-source": true } : {})}
                className={`w-full px-3 py-2.5 text-left transition-all ${
                  isActive
                    ? "bg-[var(--color-primary)]/5 border-l-2 border-l-[var(--color-primary)]"
                    : "border-l-2 border-l-transparent hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span
                    className={`truncate text-xs font-medium ${isActive ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}
                  >
                    {displayName}
                  </span>
                  <div className="flex items-center gap-1 shrink-0">
                    {hasActiveInstance && (
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
                    )}
                    {r.report_count > 0 && (
                      <span className="text-[9px] text-[var(--color-text-muted)]/60">
                        {r.report_count}
                      </span>
                    )}
                  </div>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  {isRoutineAgent && (
                    <span className="flex items-center gap-0.5 rounded bg-purple-500/10 px-1 py-0.5 text-[8px] font-bold uppercase text-purple-400">
                      <Brain className="h-2 w-2" />
                      agent
                    </span>
                  )}
                  <span className="text-[9px] text-[var(--color-text-muted)]/50 truncate">
                    {r.description}
                  </span>
                </div>
              </button>
            );
          })}
        </div>

        {/* Navigation hint */}
        {!isCompact && (
          <div className="border-t border-[var(--color-border)] px-3 py-2 text-[10px] text-[var(--color-text-muted)]/60">
            <span className="flex items-center gap-1.5">
              <span className="flex items-center gap-0.5">
                <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                  <ChevronUp className="h-2.5 w-2.5" />
                </kbd>
                <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                  <ChevronDown className="h-2.5 w-2.5" />
                </kbd>
                <span className="ml-0.5">source</span>
              </span>
              <span className="flex items-center gap-0.5">
                <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                  <ChevronLeft className="h-2.5 w-2.5" />
                </kbd>
                <kbd className="inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-0.5 text-[8px] font-medium">
                  <ChevronRight className="h-2.5 w-2.5" />
                </kbd>
                <span className="ml-0.5">report</span>
              </span>
              <kbd className="inline-flex h-4 items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-1 text-[8px] font-medium">
                esc
              </kbd>
            </span>
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="truncate text-sm font-semibold text-[var(--color-text)]">
              {effectiveActiveSource.replace(/_/g, " ")}
            </h2>
            {isAgent && (
              <span className="flex items-center gap-0.5 rounded bg-purple-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase text-purple-400">
                <Brain className="h-2.5 w-2.5" />
                {activeRoutine?.source.replace("agent:", "")}
              </span>
            )}
            {sourceInstances.length > 0 && (
              <div className="flex items-center gap-2">
                {sourceInstances.map((inst) => (
                  <div
                    key={inst.instance_id}
                    className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-[10px]"
                  >
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    <span className="text-emerald-400 capitalize">{inst.status}</span>
                    {inst.schedule?.type === "interval" && (
                      <span className="text-[var(--color-text-muted)]">
                        <Clock className="inline h-2.5 w-2.5" />{" "}
                        {inst.schedule.interval_sec as number}s
                      </span>
                    )}
                    <span className="text-[var(--color-text-muted)]">{inst.run_count} runs</span>
                    <button
                      onClick={() => stopMutation.mutate(inst.instance_id)}
                      disabled={stopMutation.isPending}
                      className="ml-0.5 rounded p-0.5 text-[var(--color-red)] hover:bg-[var(--color-red)]/10"
                      title="Stop"
                    >
                      <Square className="h-2.5 w-2.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1">
            {/* Run / Config actions — always show when routine exists */}
            {activeRoutine && server && (
              <div className="flex items-center gap-1 mr-2">
                <button
                  onClick={() => setShowConfigPanel(!showConfigPanel)}
                  className={`rounded p-1.5 transition-colors ${
                    showConfigPanel
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  }`}
                  title="Configure & Run"
                >
                  <Settings2 className="h-4 w-4" />
                </button>
                <button
                  onClick={() => runMutation.mutate()}
                  disabled={runMutation.isPending || !server}
                  className="flex items-center gap-1 rounded bg-[var(--color-primary)] px-2.5 py-1 text-[10px] font-semibold text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                  title="Run with current config"
                >
                  {runMutation.isPending ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Play className="h-3 w-3" />
                  )}
                  Run
                </button>
                {!activeRoutine.is_continuous && (
                  <ScheduleDropdown
                    onSchedule={(sec) => scheduleMutation.mutate(sec)}
                    disabled={scheduleMutation.isPending || !server}
                  />
                )}
                {filteredRoutines.length > 1 && (
                  <button
                    onClick={runAll}
                    disabled={!!runAllProgress || !server}
                    className="flex items-center gap-1 rounded bg-[var(--color-surface-hover)] px-2.5 py-1 text-[10px] font-semibold text-[var(--color-text)] transition-colors hover:bg-[var(--color-border)] disabled:opacity-50"
                    title="Run all filtered routines with default configs"
                  >
                    {runAllProgress ? (
                      <>
                        <Loader2 className="h-3 w-3 animate-spin" />
                        {runAllProgress.current}/{runAllProgress.total}
                      </>
                    ) : (
                      <>
                        <PlayCircle className="h-3 w-3" />
                        Run All
                      </>
                    )}
                  </button>
                )}
              </div>
            )}
            {/* Report navigation */}
            {reports.length > 1 && (
              <>
                <span className="mr-1 text-[10px] text-[var(--color-text-muted)]">
                  {selectedReportIdx + 1} of {reports.length}
                </span>
                <button
                  onClick={goPrevReport}
                  disabled={selectedReportIdx === 0}
                  className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
                  title="Previous report"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <button
                  onClick={goNextReport}
                  disabled={selectedReportIdx >= reports.length - 1}
                  className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
                  title="Next report"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </>
            )}
            {/* Delete */}
            {selectedReport &&
              (confirmDelete ? (
                <div className="flex items-center gap-1 ml-2">
                  <span className="text-xs text-[var(--color-red)]">Delete?</span>
                  <button
                    onClick={() => {
                      deleteMutation.mutate(selectedReport.id);
                      setConfirmDelete(false);
                    }}
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
              ))}
            {/* Report theme toggle */}
            <button
              onClick={() => setReportTheme((t) => (t === "dark" ? "light" : "dark"))}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title={`Switch to ${reportTheme === "dark" ? "light" : "dark"} report theme`}
            >
              {reportTheme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            {/* Agent chat toggle */}
            <button
              onClick={() => {
                window.dispatchEvent(
                  new KeyboardEvent("keydown", {
                    key: "k",
                    metaKey: true,
                    bubbles: true,
                  }),
                );
              }}
              className="ml-1 flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium bg-amber-500/15 text-amber-500 hover:bg-amber-500/25 border border-amber-500/30 transition-all"
              title="Agent (⌘K)"
            >
              <MessageSquare className="h-3.5 w-3.5" />
              <span>Agent</span>
            </button>
            {/* Close */}
            <button
              onClick={onClose}
              className="ml-1 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Report meta bar */}
        {selectedReport && (
          <div className="flex items-center gap-3 border-b border-[var(--color-border)]/50 px-4 py-1.5 text-[10px] text-[var(--color-text-muted)]">
            <span>{selectedReport.title}</span>
            <span>{new Date(selectedReport.created_at).toLocaleString()}</span>
            {selectedReport.tags.map((tag) => (
              <span
                key={tag}
                className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[9px]"
              >
                #{tag}
              </span>
            ))}
          </div>
        )}

        {/* Config panel (collapsible) */}
        {showConfigPanel && activeRoutine && (
          <div className="border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-[11px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                Configuration
              </h3>
              <button
                onClick={() => setShowConfigPanel(false)}
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            {Object.keys(activeRoutine.fields).length > 0 ? (
              <RoutineConfigForm
                fields={activeRoutine.fields}
                values={configValues}
                onChange={(key, value) => setConfigValues((prev) => ({ ...prev, [key]: value }))}
              />
            ) : (
              <p className="text-xs text-[var(--color-text-muted)]">No configurable fields</p>
            )}
            {runMutation.isError && (
              <p className="mt-2 text-xs text-[var(--color-red)]">
                {(runMutation.error as Error).message}
              </p>
            )}
          </div>
        )}

        {/* Report timeline strip at top */}
        {reports.length > 1 && (
          <div className="flex min-w-0 items-center overflow-hidden border-b border-[var(--color-border)]/50">
            {timelineOverflow && (
              <button
                type="button"
                onClick={() => scrollTimeline("left")}
                disabled={!canScrollTimelineLeft}
                className="shrink-0 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:cursor-default disabled:opacity-30"
                title="Scroll to newer runs"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
            )}
            <div
              ref={timelineRef}
              className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden px-4 py-1.5 scrollbar-none"
            >
              <div ref={timelineContentRef} className="flex w-max items-center gap-1">
                {reports.map((r, idx) => (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => setSelectedReportIdx(idx)}
                    {...(idx === selectedReportIdx ? { "data-active-report": true } : {})}
                    className={`shrink-0 rounded px-2 py-1 text-[10px] transition-all ${
                      idx === selectedReportIdx
                        ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-medium"
                        : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                    }`}
                    title={r.title}
                  >
                    {formatAgo(r.created_at)}
                  </button>
                ))}
              </div>
            </div>
            {timelineOverflow && (
              <button
                type="button"
                onClick={() => scrollTimeline("right")}
                disabled={!canScrollTimelineRight}
                className="shrink-0 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:cursor-default disabled:opacity-30"
                title="Scroll to older runs"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}

        {/* Report content */}
        <div className="relative min-h-0 flex-1">
          {loadingReports ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-[var(--color-text-muted)]" />
            </div>
          ) : !selectedReport ? (
            // No reports — prompt to run for the first time
            <div className="flex h-full flex-col items-center justify-center text-center px-8">
              <Zap className="mb-3 h-10 w-10 text-[var(--color-text-muted)]/20" />
              <p className="text-sm font-medium text-[var(--color-text)]">No reports yet</p>
              <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                {activeRoutine?.description ?? "Run this routine to generate your first report."}
              </p>
              {activeRoutine && server && (
                <button
                  onClick={() => runMutation.mutate()}
                  disabled={runMutation.isPending}
                  className="mt-4 flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                >
                  {runMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                  Run for the first time
                </button>
              )}
              {runMutation.isError && (
                <p className="mt-2 text-xs text-[var(--color-red)]">
                  {(runMutation.error as Error).message}
                </p>
              )}
            </div>
          ) : (
            <iframe
              ref={iframeRef}
              src={`/reports/${selectedReport.filename}`}
              className="h-full w-full border-0"
              title={selectedReport.title}
            />
          )}

          {/* Chevron overlays for report navigation */}
          {selectedReport && selectedReportIdx > 0 && (
            <button
              onClick={goPrevReport}
              className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-black/40 p-2 text-white/60 hover:bg-black/60 hover:text-white transition-all"
            >
              <ChevronLeft className="h-5 w-5" />
            </button>
          )}
          {selectedReport && selectedReportIdx < reports.length - 1 && (
            <button
              onClick={goNextReport}
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-black/40 p-2 text-white/60 hover:bg-black/60 hover:text-white transition-all"
            >
              <ChevronRight className="h-5 w-5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function formatAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
