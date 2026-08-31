import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Brain,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock,
  Download,
  Loader2,
  Play,
  PlayCircle,
  Settings2,
  Trash2,
  X,
  Zap,
  AlertTriangle,
  Code,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type RoutineInfo, type RoutineInstance, api } from "@/lib/api";
import { toMs } from "@/lib/formatters";
import { buildConfigValues, formatAgo, formatInterval, invalidateRoutineQueries, saveConfig } from "@/lib/routineUtils";
import { useViewFacts } from "@/lib/viewFacts";
import { useServer } from "@/hooks/useServer";
import { ReportFrame } from "./ReportFrame";
import { RoutineConfigForm } from "./RoutineConfigForm";
import { RoutineHooksPanel } from "./RoutineHooksPanel";
import { RoutineResultView } from "./RoutineResultView";
import { ScheduleDropdown } from "./ScheduleDropdown";

/**
 * The conversation a run launched from here belongs to.
 *
 * Without one — the `/routines` page, an agent's own page — a run is what it
 * has always been: the dashboard's server, no conversation behind it.
 */
export interface RoutineRunContext {
  /** The server the conversation is talking to, which is where its runs go. */
  serverName: string;
  /** Resolved server-side into the conversation the run reports back to. */
  sessionKey: string;
  /** Who the run's reports are filed under, as the chat's own runs are. */
  agentSlug?: string;
}

/**
 * The routines one scope covers.
 *
 * `"all"`, the two families, or one agent by slug — the same vocabulary the
 * header's scope picker offers, kept out of the component because the picker
 * and the list have to agree about what a scope means.
 */
function inScope(routines: RoutineInfo[], scope: string): RoutineInfo[] {
  if (scope === "all") return routines;
  if (scope === "routine")
    return routines.filter((r) => !r.source.startsWith("agent:"));
  if (scope === "agent")
    return routines.filter((r) => r.source.startsWith("agent:"));
  return routines.filter((r) => r.source === `agent:${scope}`);
}

/**
 * Whose routines to list: everything, one family, or one agent.
 *
 * A native select rather than the chip row this replaces — the row was as wide
 * as the number of agents, which the 550px pane cannot spend, and it lived in a
 * sidebar that is collapsed there anyway.
 */
function ScopePicker({
  scope,
  agents,
  onChange,
}: {
  scope: string;
  agents: string[];
  onChange: (next: string) => void;
}) {
  const isAgentScope = scope !== "all" && scope !== "routine";
  return (
    <div className="relative shrink-0">
      <select
        value={scope}
        onChange={(e) => onChange(e.target.value)}
        title="Which routines this list shows"
        aria-label="Routine scope"
        className={`appearance-none rounded-md border py-1 pl-2.5 pr-6 text-[10px] font-medium cursor-pointer transition-colors focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]/40 ${
          isAgentScope
            ? "border-purple-500/30 bg-purple-500/10 text-purple-400"
            : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        }`}
      >
        <option value="all">All routines</option>
        <option value="routine">Library</option>
        <option value="agent">All agents</option>
        {agents.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-current opacity-70" />
    </div>
  );
}

interface ReportBrowserProps {
  initialSource?: string;
  /** Open on this report of {@link initialSource}, rather than its latest. */
  initialReportId?: string;
  /**
   * Open on this run of {@link initialSource}. A run that wrote no report opens
   * on its output, which is the whole of what it produced.
   */
  initialInstanceId?: string;
  initialSourceTypeFilter?: string;
  instances: RoutineInstance[];
  onClose: () => void;
  /**
   * Rendered inside the workspace pane: fill the container instead of the
   * viewport, and leave the window's keys and the Close button to the host.
   */
  hosted?: boolean;
  /** Run and schedule as this conversation, on its server. */
  runContext?: RoutineRunContext;
}

export function ReportBrowser({
  instances,
  initialSource,
  initialReportId,
  initialInstanceId,
  initialSourceTypeFilter,
  onClose,
  hosted = false,
  runContext,
}: ReportBrowserProps) {
  const { server } = useServer();
  // The conversation's server wins over the dashboard's selector: a routine
  // asked for beside a chat runs where that chat is pointed, not where a page
  // the reader last touched happens to point.
  const runServer = runContext?.serverName || server;
  const qc = useQueryClient();
  const [sourceTypeFilter, setSourceTypeFilter] = useState<string>(initialSourceTypeFilter || "all");
  // 550px of pane cannot hold a 256px list and a report: hosted opens on the
  // 48px rail, the page keeps its list, and the toggle still works either way.
  const [isCompact, setIsCompact] = useState(hosted);
  const [showConfigPanel, setShowConfigPanel] = useState(false);
  const [showNotifyPanel, setShowNotifyPanel] = useState(false);
  const [showSourceModal, setShowSourceModal] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Fetch all routines for the sidebar
  const { data: routines = [] } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
  });

  const [pickedSource, setActiveSource] = useState(initialSource ?? "");
  /**
   * Reports, or the run's own text.
   *
   * A run that wrote a report is read as that report; one that only answered in
   * text — most one-shots — has nothing in the report index, and its output is
   * the whole of what it produced. Both are the same routine, so they are two
   * views of this pane rather than two places to look.
   */
  const [view, setView] = useState<"report" | "output">(
    initialInstanceId && !initialReportId ? "output" : "report",
  );

  /** Picking another routine is always a fresh read of it. */
  const selectSource = useCallback((name: string) => {
    setActiveSource(name);
    setView("report");
  }, []);

  /**
   * The routine on screen, said the way the library says it.
   *
   * Two spellings reach here for one routine. A run names it as the store
   * registered it — `{slug}/{name}` for an agent's own — while a report names it
   * as the report index filed it, which is the bare name. Both have to land on
   * the same routine, or the header's Run and Config would act on nothing.
   */
  const activeRoutine = useMemo(() => {
    if (!pickedSource) return undefined;
    return (
      routines.find((r) => r.name === pickedSource) ??
      routines.find(
        (r) => r.name.split("/").pop() === pickedSource.split("/").pop(),
      )
    );
  }, [routines, pickedSource]);
  const activeSource = activeRoutine?.name ?? pickedSource;

  /**
   * The scope the list actually shows.
   *
   * A row opened from a conversation may point at a shared or general-library
   * routine that the agent's own scope excludes, and a list that does not
   * contain what the pane is showing is worse than a wider one — so the pane
   * widens itself rather than hiding what the reader just clicked.
   */
  const effectiveScope =
    activeRoutine &&
    !inScope(routines, sourceTypeFilter).some(
      (r) => r.name === activeRoutine.name,
    )
      ? "all"
      : sourceTypeFilter;

  // Filter routines by source type
  const filteredRoutines = useMemo(
    () => inScope(routines, effectiveScope),
    [routines, effectiveScope],
  );

  // Set initial source once routines load if not set — pick from filtered list
  useEffect(() => {
    if (!pickedSource && filteredRoutines.length > 0) {
      setActiveSource(filteredRoutines[0].name);
    }
  }, [pickedSource, filteredRoutines]);

  /** Narrowing the list moves the reader into it, rather than stranding them. */
  const changeScope = useCallback(
    (next: string) => {
      setSourceTypeFilter(next);
      const scoped = inScope(routines, next);
      if (!scoped.some((r) => r.name === activeSource)) {
        selectSource(scoped[0]?.name ?? "");
      }
    },
    [routines, activeSource, selectSource],
  );

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

  const isAgent = activeRoutine?.source.startsWith("agent:") ?? false;

  // Reports for active source — poll when a scheduled instance is active
  const hasScheduledInstance = instances.some(
    (i) => i.routine_name === activeSource && (i.status === "running" || i.status === "scheduled"),
  );
  const { data: reportsData, isLoading: loadingReports } = useQuery({
    queryKey: ["routine-reports", activeSource],
    queryFn: () => api.getRoutineReports(activeSource),
    enabled: !!activeSource,
    refetchInterval: hasScheduledInstance ? 10_000 : false,
  });
  // Memoized: identity feeds the report that opens first, and a new array on
  // every render would recompute it on every render.
  const reports = useMemo(() => reportsData?.reports ?? [], [reportsData]);

  // Source code query (lazy)
  const { data: sourceData } = useQuery({
    queryKey: ["routine-source", activeSource],
    queryFn: () => api.getRoutineSource(activeSource),
    enabled: showSourceModal && !!activeSource,
  });

  /**
   * Which report is open: the reader's pick, else the one the pane was opened
   * for.
   *
   * A dock row points at one run, not at the newest, so "no pick yet" resolves
   * to that report rather than to index 0 — derived rather than corrected in an
   * effect, so the right report is on screen in the first paint and a poll that
   * refreshes the list can never drag the reader back to it.
   */
  const [pickedReportIdx, setSelectedReportIdx] = useState<number | null>(null);
  const openedOnIdx = useMemo(() => {
    if (!initialReportId) return 0;
    return Math.max(
      0,
      reports.findIndex((r) => r.id === initialReportId),
    );
  }, [reports, initialReportId]);
  const selectedReportIdx = pickedReportIdx ?? openedOnIdx;
  const selectedReport = reports[selectedReportIdx] ?? null;

  // Another routine is read from the top — back to "no pick", which is the
  // newest for a routine the pane was not opened on.
  useEffect(() => {
    setSelectedReportIdx(null);
  }, [activeSource]);

  // Active instances for current source
  const sourceInstances = useMemo(
    () => instances.filter((i) => i.routine_name === activeSource && (i.status === "running" || i.status === "scheduled")),
    [instances, activeSource],
  );

  /**
   * The run behind the Output view: the one the reader clicked, else this
   * routine's most recent. Runs live in memory, so there may be none — the
   * toggle is offered only when there is something to toggle to.
   */
  const outputRun = useMemo(() => {
    const forSource = instances.filter((i) => i.routine_name === activeSource);
    const clicked =
      initialInstanceId &&
      forSource.find((i) => i.instance_id === initialInstanceId);
    if (clicked) return clicked;
    return (
      [...forSource].sort(
        (a, b) =>
          toMs(b.last_run_at ?? b.created_at) -
          toMs(a.last_run_at ?? a.created_at),
      )[0] ?? null
    );
  }, [instances, activeSource, initialInstanceId]);

  // `last_result` on the list is truncated; the sections, tables and chart of a
  // run live behind its own route, so the view that shows them fetches it.
  const { data: outputRunFull } = useQuery({
    queryKey: ["routine-instance", outputRun?.instance_id],
    queryFn: () => api.getRoutineInstance(outputRun!.instance_id),
    enabled: view === "output" && !!outputRun,
  });
  const shownRun = outputRunFull ?? outputRun;
  // Offered while there is a run with something to say — and kept while the
  // reader is looking at it, so the switch cannot vanish from under them.
  const hasOutput =
    view === "output" ||
    !!(outputRun && (outputRun.has_result || outputRun.last_result || outputRun.error));

  // Latest failed instance for error display
  const latestFailedInstance = useMemo(
    () => instances.find((i) => i.routine_name === activeSource && i.status === "failed"),
    [instances, activeSource],
  );

  // Config state: merge routine fields with saved localStorage values
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (!activeRoutine) return;
    setConfigValues(buildConfigValues(activeRoutine));
    setShowConfigPanel(false);
  }, [activeSource, activeRoutine]);

  // Track running instance to poll for completion
  const [pollingInstanceId, setPollingInstanceId] = useState<string | null>(null);

  const { data: polledInstance } = useQuery({
    queryKey: ["routine-instance", pollingInstanceId],
    queryFn: () => api.getRoutineInstance(pollingInstanceId!),
    enabled: !!pollingInstanceId,
    refetchInterval: 2000,
  });

  // When polled instance completes, refresh reports
  useEffect(() => {
    if (polledInstance && polledInstance.status !== "running") {
      setPollingInstanceId(null);
      invalidateRoutineQueries(qc, activeSource);
    }
  }, [polledInstance, activeSource, qc]);

  const runMutation = useMutation({
    mutationFn: () =>
      api.runRoutine(runServer!, activeSource, configValues, {
        sessionKey: runContext?.sessionKey,
        attributeTo: runContext?.agentSlug,
      }),
    onSuccess: (data) => {
      setPollingInstanceId(data.instance_id);
      qc.invalidateQueries({ queryKey: ["routine-instances"] });
      setShowConfigPanel(false);
    },
  });

  const scheduleMutation = useMutation({
    mutationFn: (intervalSec: number) =>
      api.scheduleRoutine(runServer!, activeSource, configValues, intervalSec),
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
      qc.invalidateQueries({ queryKey: ["routine-reports", activeSource] });
      if (selectedReport?.id === id) {
        setSelectedReportIdx(Math.max(0, selectedReportIdx - 1));
      }
    },
  });

  // Run All state
  const [runAllProgress, setRunAllProgress] = useState<{ current: number; total: number } | null>(null);

  const runAll = useCallback(async () => {
    if (!runServer || filteredRoutines.length === 0) return;
    const toRun = filteredRoutines;
    setRunAllProgress({ current: 0, total: toRun.length });
    for (let i = 0; i < toRun.length; i++) {
      setRunAllProgress({ current: i + 1, total: toRun.length });
      const routine = toRun[i];
      const cfg = buildConfigValues(routine);
      try {
        await api.runRoutine(runServer, routine.name, cfg, {
          sessionKey: runContext?.sessionKey,
          attributeTo: runContext?.agentSlug,
        });
      } catch {
        // continue with remaining routines
      }
    }
    setRunAllProgress(null);
    invalidateRoutineQueries(qc);
    qc.invalidateQueries({ queryKey: ["routine-reports"] });
  }, [runServer, runContext, filteredRoutines, qc]);

  const [confirmDelete, setConfirmDelete] = useState(false);

  /**
   * Download the report body.
   *
   * Report HTML is authenticated (SEC-112), so a bare `href` to it no longer
   * works: fetch it with the token in a header and save the response as a blob.
   */
  const downloadReport = useCallback(async () => {
    if (!selectedReport) return;
    // Hydrated: the saved file is opened at `file://`, where the shared plotly
    // bundle the stored report references cannot resolve (PERF-267).
    const html = await api.getReportHtml(selectedReport.id, { hydrate: true });
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = selectedReport.filename;
    link.click();
    URL.revokeObjectURL(url);
  }, [selectedReport]);

  // Keyboard navigation
  const activeSourceIdx = filteredRoutines.findIndex((r) => r.name === activeSource);

  const goSourceUp = useCallback(() => {
    if (activeSourceIdx > 0) {
      selectSource(filteredRoutines[activeSourceIdx - 1].name);
    }
  }, [activeSourceIdx, filteredRoutines, selectSource]);

  const goSourceDown = useCallback(() => {
    if (activeSourceIdx < filteredRoutines.length - 1) {
      selectSource(filteredRoutines[activeSourceIdx + 1].name);
    }
  }, [activeSourceIdx, filteredRoutines, selectSource]);

  const goPrevReport = useCallback(() => {
    if (selectedReportIdx > 0) setSelectedReportIdx(selectedReportIdx - 1);
  }, [selectedReportIdx]);

  const goNextReport = useCallback(() => {
    if (selectedReportIdx < reports.length - 1)
      setSelectedReportIdx(selectedReportIdx + 1);
  }, [selectedReportIdx, reports.length]);

  /**
   * The browser's own keys.
   *
   * Full screen they are the window's, because the browser *is* the window.
   * Hosted they are the container's: a live composer sits beside the pane, and
   * a "k" typed into the chat that paged the report list would make the whole
   * arrangement feel broken. Escape is the host's too — {@link WorkspaceSheet}
   * owns closing the pane, and Escape belongs to the conversation — so hosted
   * it only dismisses this browser's own panels.
   */
  const handleKey = useCallback(
    (e: Pick<KeyboardEvent, "key" | "target" | "preventDefault">) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "ArrowUp") { goSourceUp(); e.preventDefault(); }
      else if (e.key === "ArrowDown") { goSourceDown(); e.preventDefault(); }
      else if (e.key === "ArrowLeft") { goPrevReport(); e.preventDefault(); }
      else if (e.key === "ArrowRight") { goNextReport(); e.preventDefault(); }
      else if (e.key === "Escape") {
        if (showSourceModal) setShowSourceModal(false);
        else if (showConfigPanel) setShowConfigPanel(false);
        else if (showNotifyPanel) setShowNotifyPanel(false);
        else if (!hosted) onClose();
        else return;
        e.preventDefault();
      }
    },
    [goSourceUp, goSourceDown, goPrevReport, goNextReport, onClose, hosted, showConfigPanel, showNotifyPanel, showSourceModal],
  );

  useEffect(() => {
    if (hosted) return;
    const handler = (e: KeyboardEvent) => handleKey(e);
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [hosted, handleKey]);

  // Hosted, the keys only reach the browser while it has focus — so it takes
  // focus once, on open. The click that opened it already left the composer.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (hosted) rootRef.current?.focus({ preventScroll: true });
  }, [hosted]);

  // Scroll active source into view
  useEffect(() => {
    const el = sidebarRef.current?.querySelector("[data-active-source]");
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeSource]);

  // Tell the chat what report is open, for as long as the reader is (FEAT-059).
  useViewFacts(() =>
    view === "output" && shownRun
      ? {
          label: "Routine output",
          subject: `the output of the last ${activeSource} run`,
        }
      : selectedReport && view === "report"
        ? {
            label: "Routine report",
            subject: `report "${selectedReport.title}" (${selectedReport.filename}) from ${activeSource}`,
          }
        : null,
  );

  return (
    <div
      ref={rootRef}
      // Hosted, the pane already sizes and paints; full screen, this is the page.
      className={
        hosted
          ? "flex min-h-0 w-full flex-1 overflow-hidden outline-none"
          : "fixed inset-0 z-50 flex bg-[var(--color-bg)]"
      }
      tabIndex={hosted ? -1 : undefined}
      onKeyDown={hosted ? handleKey : undefined}
    >
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
            {isCompact ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
          </button>
        </div>

        {/* Routine list */}
        <div ref={sidebarRef} className="flex-1 overflow-y-auto scrollbar-thin">
          {filteredRoutines.map((r) => {
            const isActive = r.name === activeSource;
            const hasActiveInstance = instances.some(
              (i) => i.routine_name === r.name && (i.status === "running" || i.status === "scheduled"),
            );
            const isRoutineAgent = r.source.startsWith("agent:");
            const displayName = r.name.replace(/_/g, " ");

            if (isCompact) {
              return (
                <button
                  key={r.name}
                  onClick={() => selectSource(r.name)}
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
                onClick={() => selectSource(r.name)}
                {...(isActive ? { "data-active-source": true } : {})}
                className={`w-full px-3 py-2.5 text-left transition-all ${
                  isActive
                    ? "bg-[var(--color-primary)]/5 border-l-2 border-l-[var(--color-primary)]"
                    : "border-l-2 border-l-transparent hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className={`truncate text-xs font-medium ${isActive ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}>
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
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar — wraps rather than widening the column: at pane width the
            actions drop to a second row, and the routine's name keeps its own.
            Left to overflow it would lay the whole column out wider than the
            pane and clip the report off the right of the screen. */}
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-[var(--color-border)] px-4 py-2">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            {/* Whose routines this pane is listing. In the header rather than
                in the sidebar because the sidebar is a 48px rail whenever this
                is hosted beside a chat, and a filter you can only reach by
                widening the list is a filter nobody uses. */}
            {hasAgents && (
              <ScopePicker
                scope={effectiveScope}
                agents={agentNames}
                onChange={changeScope}
              />
            )}
            <h2 className="truncate text-sm font-semibold text-[var(--color-text)]">
              {activeSource.replace(/_/g, " ")}
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
                  <div key={inst.instance_id} className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-[10px]">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    <span className="text-emerald-400 capitalize">{inst.status}</span>
                    {inst.schedule?.type === "interval" && (
                      <span className="text-[var(--color-text-muted)]">
                        <Clock className="inline h-2.5 w-2.5" /> {formatInterval(inst.schedule.interval_sec as number)}
                      </span>
                    )}
                    <span className="text-[var(--color-text-muted)]">{inst.run_count} runs</span>
                    <button
                      onClick={() => stopMutation.mutate(inst.instance_id)}
                      disabled={stopMutation.isPending}
                      className="ml-0.5 rounded p-0.5 text-[var(--color-red)] hover:bg-[var(--color-red)]/10"
                      title="Stop"
                    >
                      <Trash2 className="h-2.5 w-2.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1">
            {/* Run / Config actions — always show when routine exists */}
            {activeRoutine && runServer && (
              <div className="flex items-center gap-1 mr-2">
                <button
                  onClick={() => setShowSourceModal(true)}
                  className="flex items-center gap-1 rounded px-2 py-1 text-[10px] font-semibold text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  title="View source code"
                >
                  <Code className="h-3.5 w-3.5" />
                  Source
                </button>
                <button
                  onClick={() => {
                    setShowConfigPanel((v) => !v);
                    setShowNotifyPanel(false);
                  }}
                  className={`flex items-center gap-1 rounded px-2 py-1 text-[10px] font-semibold transition-colors ${
                    showConfigPanel
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  }`}
                  title="Configure & Run"
                >
                  <Settings2 className="h-3.5 w-3.5" />
                  Config
                </button>
                <button
                  onClick={() => {
                    setShowNotifyPanel((v) => !v);
                    setShowConfigPanel(false);
                  }}
                  className={`flex items-center gap-1 rounded px-2 py-1 text-[10px] font-semibold transition-colors ${
                    showNotifyPanel
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  }`}
                  title="Post-run notifications"
                >
                  <Bell className="h-3.5 w-3.5" />
                  Notify
                </button>
                <button
                  onClick={() => runMutation.mutate()}
                  disabled={runMutation.isPending || !runServer}
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
                    disabled={scheduleMutation.isPending || !runServer}
                  />
                )}
                {filteredRoutines.length > 1 && (
                  <button
                    onClick={runAll}
                    disabled={!!runAllProgress || !runServer}
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
            {/* What this routine's last run said, when it said it in text
                rather than in a report — and the way back to the reports. */}
            {hasOutput && (
              <div className="mr-1 flex items-center gap-0.5 rounded bg-[var(--color-surface-hover)] p-0.5">
                {(["report", "output"] as const).map((id) => (
                  <button
                    key={id}
                    onClick={() => setView(id)}
                    className={`rounded px-2 py-0.5 text-[10px] font-semibold capitalize transition-colors ${
                      view === id
                        ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                        : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    }`}
                    title={
                      id === "report"
                        ? "The pages this routine wrote"
                        : "What its last run handed back"
                    }
                  >
                    {id}
                  </button>
                ))}
              </div>
            )}
            {/* Report navigation */}
            {view === "report" && reports.length > 1 && (
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
            {/* Download */}
            {view === "report" && selectedReport && (
              <button
                onClick={downloadReport}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                title="Download report"
              >
                <Download className="h-4 w-4" />
              </button>
            )}
            {/* Delete */}
            {view === "report" && selectedReport && (
              confirmDelete ? (
                <div className="flex items-center gap-1 ml-2">
                  <span className="text-xs text-[var(--color-red)]">Delete?</span>
                  <button
                    onClick={() => { deleteMutation.mutate(selectedReport.id); setConfirmDelete(false); }}
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
            {/* Close — the sheet's header has one when hosted */}
            {!hosted && (
              <button
                onClick={onClose}
                className="ml-1 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                title="Close (Esc)"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* Schedule failure — surfaced outside the collapsible panels so it is
            always visible after a failed ScheduleDropdown action (CORR-097) */}
        {scheduleMutation.isError && (
          <div className="border-b border-[var(--color-border)] bg-[var(--color-red)]/5 px-4 py-2">
            <p className="text-xs text-[var(--color-red)]">
              Could not schedule {activeSource.replace(/_/g, " ")}: {(scheduleMutation.error as Error).message}
            </p>
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
                onChange={(key, value) => {
                  setConfigValues((prev) => {
                    const next = { ...prev, [key]: value };
                    saveConfig(activeSource, next);
                    return next;
                  });
                }}
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

        {/* Notifications panel (collapsible) */}
        {showNotifyPanel && activeRoutine && (
          <div className="border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-[11px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                Notifications
              </h3>
              <button
                onClick={() => setShowNotifyPanel(false)}
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            <RoutineHooksPanel routineName={activeSource} collapsible={false} />
          </div>
        )}

        {/* Report timeline strip at top */}
        {view === "report" && reports.length > 1 && (
          <div className="flex items-center gap-1 border-b border-[var(--color-border)]/50 px-4 py-1.5">
            {reports.slice(0, 10).map((r, idx) => (
              <button
                key={r.id}
                onClick={() => setSelectedReportIdx(idx)}
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
            {reports.length > 10 && (
              <div className="relative shrink-0 ml-auto">
                <select
                  value={selectedReportIdx >= 10 ? selectedReportIdx : ""}
                  onChange={(e) => {
                    const val = e.target.value;
                    if (val !== "") setSelectedReportIdx(Number(val));
                  }}
                  className={`appearance-none rounded-md pl-2.5 pr-7 py-1 text-[10px] font-medium cursor-pointer transition-colors focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]/40 ${
                    selectedReportIdx >= 10
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)] border border-[var(--color-primary)]/30"
                      : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:text-[var(--color-text)] hover:border-[var(--color-text-muted)]/30"
                  }`}
                >
                  <option value="" disabled>
                    +{reports.length - 10} older reports
                  </option>
                  {reports.slice(10).map((r, i) => (
                    <option key={r.id} value={i + 10}>
                      {new Date(r.created_at).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                      {r.title !== reports[0]?.title ? ` — ${r.title}` : ""}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-[var(--color-text-muted)]" />
              </div>
            )}
          </div>
        )}

        {/* Report content */}
        <div className="relative flex-1">
          {view === "output" ? (
            <div className="h-full overflow-auto px-4 py-3">
              {!shownRun ? (
                <p className="text-xs text-[var(--color-text-muted)]">
                  This routine has no run in memory.
                </p>
              ) : shownRun.error ? (
                <pre className="whitespace-pre-wrap break-words font-mono text-xs text-[var(--color-red)]">
                  {shownRun.error}
                </pre>
              ) : shownRun.has_result || shownRun.result_text ? (
                <RoutineResultView instance={shownRun} />
              ) : (
                <p className="whitespace-pre-wrap text-xs text-[var(--color-text-muted)]">
                  {shownRun.last_result || "(no output yet)"}
                </p>
              )}
            </div>
          ) : loadingReports ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-[var(--color-text-muted)]" />
            </div>
          ) : !selectedReport ? (
            // No reports — show error if failed, otherwise prompt to run
            <div className="flex h-full flex-col items-center justify-center text-center px-8">
              {(polledInstance?.status === "failed" || latestFailedInstance) ? (
                <>
                  <div className="w-full max-w-lg rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/5 p-5">
                    <div className="flex items-center gap-2 mb-3">
                      <AlertTriangle className="h-5 w-5 text-[var(--color-red)]" />
                      <span className="text-sm font-semibold text-[var(--color-red)]">Routine Failed</span>
                    </div>
                    <pre className="whitespace-pre-wrap break-words text-left font-mono text-xs text-[var(--color-text-muted)] bg-[var(--color-surface)] rounded p-3 max-h-60 overflow-y-auto">
                      {(polledInstance?.status === "failed" ? polledInstance.error : latestFailedInstance?.error) || "Unknown error"}
                    </pre>
                    {activeRoutine && runServer && (
                      <button
                        onClick={() => runMutation.mutate()}
                        disabled={runMutation.isPending}
                        className="mt-4 flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                      >
                        {runMutation.isPending ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Play className="h-3.5 w-3.5" />
                        )}
                        Retry
                      </button>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <Zap className="mb-3 h-10 w-10 text-[var(--color-text-muted)]/20" />
                  <p className="text-sm font-medium text-[var(--color-text)]">
                    No reports yet
                  </p>
                  <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                    {activeRoutine?.description ?? "Run this routine to generate your first report."}
                  </p>
                  {activeRoutine && Object.keys(activeRoutine.fields).length > 0 && (
                    <div className="mt-4 w-full max-w-md rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                      <h3 className="mb-2 text-[11px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                        Configuration
                      </h3>
                      <RoutineConfigForm
                        fields={activeRoutine.fields}
                        values={configValues}
                        onChange={(key, value) => {
                          setConfigValues((prev) => {
                            const next = { ...prev, [key]: value };
                            saveConfig(activeSource, next);
                            return next;
                          });
                        }}
                      />
                    </div>
                  )}
                  {activeRoutine && runServer && (
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
                </>
              )}
            </div>
          ) : (
            <ReportFrame reportId={selectedReport.id} title={selectedReport.title} />
          )}

          {/* Chevron overlays for report navigation */}
          {view === "report" && selectedReport && selectedReportIdx > 0 && (
            <button
              onClick={goPrevReport}
              className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-black/40 p-2 text-white/60 hover:bg-black/60 hover:text-white transition-all"
              title="Previous report"
              aria-label="Previous report"
            >
              <ChevronLeft className="h-5 w-5" />
            </button>
          )}
          {view === "report" &&
            selectedReport &&
            selectedReportIdx < reports.length - 1 && (
            <button
              onClick={goNextReport}
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-black/40 p-2 text-white/60 hover:bg-black/60 hover:text-white transition-all"
              title="Next report"
              aria-label="Next report"
            >
              <ChevronRight className="h-5 w-5" />
            </button>
          )}
        </div>
      </div>

      {/* Source code modal */}
      {showSourceModal && (
        <div className="fixed inset-0 z-[60] flex flex-col bg-[var(--color-bg)]">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
            <div className="flex items-center gap-3 min-w-0">
              <Code className="h-4 w-4 text-[var(--color-text-muted)]" />
              <span className="text-sm font-semibold text-[var(--color-text)]">
                {sourceData?.filename ?? activeSource}
              </span>
            </div>
            <button
              onClick={() => setShowSourceModal(false)}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex-1 overflow-auto bg-[var(--color-surface)]">
            {sourceData ? (
              <pre className="p-0 m-0 text-xs leading-relaxed">
                {sourceData.source.split("\n").map((line, i) => (
                  <div key={i} className="flex hover:bg-[var(--color-surface-hover)]">
                    <span className="sticky left-0 w-12 shrink-0 select-none bg-[var(--color-surface)] pr-3 text-right font-mono text-[var(--color-text-muted)]/40 border-r border-[var(--color-border)]/30">
                      {i + 1}
                    </span>
                    <code className="pl-4 font-mono text-[var(--color-text)] whitespace-pre">{line}</code>
                  </div>
                ))}
              </pre>
            ) : (
              <div className="flex h-full items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-[var(--color-text-muted)]" />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
