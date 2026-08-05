import { useQuery } from "@tanstack/react-query";
import { FileText, Loader2 } from "lucide-react";
import { useState } from "react";

import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { ReportFrame } from "@/components/routines/ReportFrame";
import { RoutineResultView } from "@/components/routines/RoutineResultView";
import { api, type ReportSummary, type RoutineInstance } from "@/lib/api";
import { formatRelativeTime, toMs } from "@/lib/formatters";
import { formatRoutineName } from "@/lib/routineUtils";

/**
 * A row in the dock: one routine run.
 *
 * Two things count as a run, because the two live in different stores. An
 * *instance* is a run this process still holds — it may be running right now,
 * and it carries the text the caller got back. A *report* is the HTML a finished
 * run rendered, which outlives the process. A run that is both appears once, as
 * an instance, and the sheet offers its report alongside its output.
 */
type Run =
  | { kind: "instance"; key: string; at: number; instance: RoutineInstance }
  | { kind: "report"; key: string; at: number; report: ReportSummary };

/**
 * What the agent on the other end has been running.
 *
 * Scoped by agent, not by conversation: routine runs carry no conversation
 * provenance (the store is shared with the scheduler and Telegram), and "this
 * agent's recent runs" is the question the dock is being asked. When the
 * conversation is unbound — the Condor assistant — the same list runs
 * unfiltered, which is the user's own recent runs.
 *
 * The instance store is in-memory, so on its own this column forgets every run
 * made before the last restart. The report index is on disk and does not, which
 * is why the history comes from there.
 */
export function DockRoutines({
  instances,
  agentSlug,
}: {
  instances: RoutineInstance[];
  /** Bound agent's slug, or "" for the unbound Condor conversation. */
  agentSlug: string;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);

  // Only fetched while the section is open — the dock renders children behind
  // its own collapse, so this query starts with the first expand.
  const { data: reportData } = useQuery({
    queryKey: ["reports", "routine", agentSlug],
    queryFn: () =>
      api.getReports({
        source_type: "routine",
        agent: agentSlug || undefined,
        limit: 40,
      }),
    refetchInterval: 15000,
  });

  // Agent routines are stored keyed `{agent_slug}/{name}` — the same prefix the
  // per-agent routes filter on.
  const prefix = `${agentSlug}/`;
  const mine = instances.filter(
    (i) => !agentSlug || i.routine_name.startsWith(prefix),
  );

  // An instance that rendered a report owns that report's row, so the run is
  // not listed twice.
  const claimed = new Set(
    mine.map((i) => i.report_id).filter((id): id is string => !!id),
  );

  const runs: Run[] = [
    ...mine.map((i) => ({
      kind: "instance" as const,
      key: `i:${i.instance_id}`,
      at: toMs(i.last_run_at ?? i.created_at),
      instance: i,
    })),
    ...(reportData?.reports ?? [])
      .filter((r) => !claimed.has(r.id))
      .map((r) => ({
        kind: "report" as const,
        key: `r:${r.id}`,
        at: toMs(r.created_at),
        report: r,
      })),
  ].sort((a, b) => b.at - a.at);

  const open = runs.find((r) => r.key === openKey);

  return (
    <>
      {runs.length === 0 ? (
        <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
          {agentSlug ? "This agent has no routine runs." : "No routine runs yet."}
        </p>
      ) : (
        <div className="space-y-px">
          {runs.map((run) =>
            run.kind === "instance" ? (
              <InstanceRow
                key={run.key}
                instance={run.instance}
                onOpen={() => setOpenKey(run.key)}
              />
            ) : (
              <ReportRow
                key={run.key}
                report={run.report}
                onOpen={() => setOpenKey(run.key)}
              />
            ),
          )}
        </div>
      )}

      {open?.kind === "instance" && (
        <RoutineRunSheet
          instance={open.instance}
          onClose={() => setOpenKey(null)}
        />
      )}
      {open?.kind === "report" && (
        <ReportSheet report={open.report} onClose={() => setOpenKey(null)} />
      )}
    </>
  );
}

// ── Rows ──

function Row({
  dot,
  title,
  meta,
  detail,
  hint,
  onOpen,
}: {
  dot: string;
  title: string;
  meta: string;
  detail?: string;
  hint?: string;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="block w-full px-3 py-1.5 text-left hover:bg-[var(--color-surface-hover)]"
      title={hint}
    >
      <span className="flex items-center gap-1.5">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
        <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--color-text)]">
          {title}
        </span>
      </span>
      <span className="block truncate pl-3 text-[10px] text-[var(--color-text-muted)]">
        {meta}
      </span>
      {detail && (
        <span className="block truncate pl-3 text-[10px] text-[var(--color-text-muted)]">
          {detail}
        </span>
      )}
    </button>
  );
}

function InstanceRow({
  instance,
  onOpen,
}: {
  instance: RoutineInstance;
  onOpen: () => void;
}) {
  return (
    <Row
      dot={
        instance.error
          ? "bg-red-400"
          : instance.status === "running"
            ? "bg-emerald-400 animate-pulse"
            : instance.last_run_at
              ? "bg-sky-400"
              : "bg-[var(--color-text-muted)]/50"
      }
      title={formatRoutineName(instance.routine_name)}
      meta={`${formatRelativeTime(instance.last_run_at, "never run")}${
        instance.run_count > 0
          ? ` · ${instance.run_count} run${instance.run_count !== 1 ? "s" : ""}`
          : ""
      }`}
      detail={instance.last_result?.split("\n")[0]}
      hint={instance.last_result || instance.routine_name}
      onOpen={onOpen}
    />
  );
}

function ReportRow({
  report,
  onOpen,
}: {
  report: ReportSummary;
  onOpen: () => void;
}) {
  const name = formatRoutineName(report.source_name || report.title);
  return (
    <Row
      dot="bg-[var(--color-text-muted)]/50"
      title={name}
      meta={formatRelativeTime(report.created_at, "")}
      // A report's own title usually restates the routine's name — say it once.
      detail={report.title === name ? undefined : report.title}
      hint={report.title}
      onOpen={onOpen}
    />
  );
}

// ── Sheets ──

function ReportSheet({
  report,
  onClose,
}: {
  report: ReportSummary;
  onClose: () => void;
}) {
  return (
    <WorkspaceSheet
      title={report.title}
      subtitle={formatRelativeTime(report.created_at, "")}
      onClose={onClose}
      bleed
    >
      <ReportFrame
        reportId={report.id}
        title={report.title}
        className="min-h-0 flex-1"
      />
    </WorkspaceSheet>
  );
}

/**
 * A run's full output.
 *
 * `/routines/instances` returns only the metadata and a truncated `last_result`
 * — the structured sections, tables and chart live behind the single-instance
 * route, so the sheet fetches the one run it is showing.
 *
 * When the run rendered a report, that report opens first: it is the page the
 * routine wrote, where `result_text` is only the summary handed back to whoever
 * asked. The report may have aged out of the index (the store keeps a fixed
 * number), in which case the output is all there is and is shown alone.
 */
function RoutineRunSheet({
  instance,
  onClose,
}: {
  instance: RoutineInstance;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["routine-instance", instance.instance_id],
    queryFn: () => api.getRoutineInstance(instance.instance_id),
  });
  const full = data ?? instance;

  const reportId = full.report_id;
  const { data: report, isLoading: reportLoading } = useQuery({
    queryKey: ["report", reportId],
    queryFn: () => api.getReport(reportId!),
    enabled: !!reportId,
    retry: false,
  });

  const [view, setView] = useState<"report" | "output">("report");
  const showReport = !!report && view === "report";

  return (
    <WorkspaceSheet
      title={formatRoutineName(instance.routine_name)}
      subtitle={formatRelativeTime(instance.last_run_at, "never run")}
      onClose={onClose}
      // With a report in play the sheet body becomes the layout: a tab strip and
      // whichever view owns the rest of it. Without one there is nothing to
      // switch to, so the sheet's own padding is right.
      bleed={!!report}
    >
      {report && (
        <div className="flex shrink-0 items-center gap-1 border-b border-[var(--color-border)]/50 px-6 py-2">
          {(["report", "output"] as const).map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => setView(id)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-all ${
                view === id
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              {id === "report" ? (
                <span className="flex items-center gap-1.5">
                  <FileText className="h-3 w-3" />
                  Report
                </span>
              ) : (
                id
              )}
            </button>
          ))}
        </div>
      )}

      {showReport ? (
        <ReportFrame
          reportId={report.id}
          title={report.title}
          className="min-h-0 flex-1"
        />
      ) : (
        <div className={report ? "min-h-0 flex-1 overflow-auto px-6 py-4" : ""}>
          {isLoading || reportLoading ? (
            <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading run…
            </div>
          ) : full.error ? (
            <pre className="whitespace-pre-wrap break-words font-mono text-xs text-red-300">
              {full.error}
            </pre>
          ) : full.has_result || full.result_text ? (
            <RoutineResultView instance={full} />
          ) : (
            <p className="text-xs text-[var(--color-text-muted)]">
              {full.last_result || "(no output yet)"}
            </p>
          )}
        </div>
      )}
    </WorkspaceSheet>
  );
}
