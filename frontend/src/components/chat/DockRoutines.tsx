import { useQuery } from "@tanstack/react-query";

import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import {
  ReportBrowser,
  type RoutineRunContext,
} from "@/components/routines/ReportBrowser";
import { RoutinePicker } from "@/components/routines/RoutinePicker";
import {
  api,
  type ReportSummary,
  type RoutineInfo,
  type RoutineInstance,
} from "@/lib/api";
import { formatRelativeTime, toMs } from "@/lib/formatters";
import { formatRoutineName, type RoutineScope } from "@/lib/routineUtils";

/**
 * A row in the dock: one routine run.
 *
 * Two things count as a run, because the two live in different stores. An
 * *instance* is a run this process still holds — it may be running right now,
 * and it carries the text the caller got back. A *report* is the HTML a finished
 * run rendered, which outlives the process. A run that is both appears once, as
 * an instance, and the library opens it on the report it wrote.
 */
type Run =
  | { kind: "instance"; key: string; at: number; instance: RoutineInstance }
  | { kind: "report"; key: string; at: number; report: ReportSummary };

/**
 * What the library pane opens on.
 *
 * A row fills in what it points at: the routine, the report it wrote, and the
 * run behind it, whose text output is all there is when it wrote no report.
 * `{}` is "the whole library", which is what a scope with nothing in it leaves
 * the pane on — the dock has no door of its own onto the unfocused library.
 */
export type LibraryFocus = {
  source?: string;
  reportId?: string;
  instanceId?: string;
};

/**
 * The runs this dock claims, out of every instance in the process.
 *
 * Exported because the dock's header needs the same answer the list does: a
 * count badge that disagreed with the rows under it would be worse than none.
 * The rule itself is explained on {@link DockRoutines}.
 */
export function conversationInstances(
  instances: RoutineInstance[],
  agentSlug: string,
  conversationId: string,
): RoutineInstance[] {
  const prefix = `${agentSlug}/`;
  return instances.filter((i) =>
    i.conversation_id
      ? i.conversation_id === conversationId
      : !agentSlug || i.routine_name.startsWith(prefix),
  );
}

/**
 * What has been run from this conversation, and what the agent has run lately.
 *
 * Scoped by conversation first: a run stamped with this conversation belongs
 * here whatever it is called. That is the only rule that holds for a *shared*
 * library routine (`agents/_shared/routines`), which the store registers under
 * its bare name — an agent running one produced an instance named
 * `backtest_chart`, no `{slug}/` prefix, so the name-based filter dropped it and
 * four backtests fired from this chat showed up nowhere.
 *
 * Runs with no conversation behind them — the scheduler, the dashboard, the
 * Telegram menu — fall back to the agent-prefix rule, which is still the right
 * answer for "what has this agent been running". An unbound Condor conversation
 * keeps them all: they are the user's own runs.
 *
 * The instance store is in-memory, so on its own this column forgets every run
 * made before the last restart. The report index is on disk and does not, which
 * is why the history comes from there.
 */
export function DockRoutines({
  instances,
  agentSlug,
  conversationId,
  onLibraryChange,
}: {
  instances: RoutineInstance[];
  /** Bound agent's slug, or "" for the unbound Condor conversation. */
  agentSlug: string;
  /** The conversation on screen, so its own runs are never filtered out. */
  conversationId: string;
  /** Where a row asks to be read: the dock owns the pane (see {@link ContextDock}). */
  onLibraryChange?: (focus: LibraryFocus) => void;
}) {
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

  // Agent-local routines are stored keyed `{agent_slug}/{name}` — the same
  // prefix the per-agent routes filter on. Shared and general-library ones are
  // not, which is why provenance wins where it exists.
  const mine = conversationInstances(instances, agentSlug, conversationId);

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
                onOpen={() =>
                  onLibraryChange?.({
                    source: run.instance.routine_name,
                    reportId: run.instance.report_id ?? undefined,
                    instanceId: run.instance.instance_id,
                  })
                }
              />
            ) : (
              <ReportRow
                key={run.key}
                report={run.report}
                onOpen={() =>
                  onLibraryChange?.({
                    source: run.report.source_name || undefined,
                    reportId: run.report.id,
                  })
                }
              />
            ),
          )}
        </div>
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

/**
 * The library, beside the conversation that wanted it.
 *
 * Pick a routine, configure it, run it, and read what it wrote — without the
 * chat leaving the screen. A dock row opens this same pane on the run it points
 * at, so a routine is read in one place however you reached it.
 *
 * Mounted by the dock itself rather than by the list of rows: collapsing the
 * Routines section, or the whole dock, must not take the pane down with it.
 * A reader who opened a report and tidied the column away kept neither.
 */
export function RoutineLibrarySheet({
  library,
  instances,
  routines,
  scope,
  onScopeChange,
  onSelectRoutine,
  dockOpen,
  agentName,
  runContext,
  onClose,
}: {
  library: LibraryFocus;
  instances: RoutineInstance[];
  /** The library, for the header's own picker and for naming what is open. */
  routines: RoutineInfo[];
  scope: RoutineScope;
  onScopeChange: (next: RoutineScope) => void;
  onSelectRoutine: (name: string) => void;
  /** Whether the dock's scope select is on screen — if not, this bar carries it. */
  dockOpen: boolean;
  /** Who is answering, for the bar's accessible name with nothing picked yet. */
  agentName?: string;
  runContext?: RoutineRunContext;
  onClose: () => void;
}) {
  return (
    <WorkspaceSheet
      // The bar names what is open with the control that changes it, so this
      // is only the accessible name behind it.
      title={
        library.source
          ? formatRoutineName(library.source)
          : agentName
            ? `Every routine ${agentName} can run`
            : "Routines"
      }
      header={({ zen }) => (
        <RoutinePicker
          variant="inline"
          routines={routines}
          instances={instances}
          scope={scope}
          onScopeChange={onScopeChange}
          source={library.source}
          onSelect={onSelectRoutine}
          // The scope is the dock's question while the dock is on screen; full
          // screen the sheet covers it, and a collapsed dock has none to cover,
          // so it is asked here instead of nowhere.
          parts={zen || !dockOpen ? "both" : "routine"}
          arrows
        />
      )}
      onClose={onClose}
      bleed
      // Below `xl` there is no pane, so this is today's full-screen browser.
      defaultZen
    >
      <ReportBrowser
        // Remounted per focus: opening the pane on another run is a fresh
        // read of it, not a browser that has to be talked out of the last.
        key={`${scope}:${library.source ?? ""}:${library.reportId ?? ""}:${library.instanceId ?? ""}`}
        hosted
        // The picking is the dock's (or this header's): no sidebar, no scope
        // select and no title in the pane, which is then only the report.
        externalPicker
        onSourceChange={onSelectRoutine}
        initialSource={library.source}
        initialReportId={library.reportId}
        initialInstanceId={library.instanceId}
        initialSourceTypeFilter={scope}
        instances={instances}
        runContext={runContext}
        onClose={onClose}
      />
    </WorkspaceSheet>
  );
}
