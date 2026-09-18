import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, FileText, MessageSquareQuote } from "lucide-react";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { DeploymentLedger } from "@/components/agent/lab/DeploymentLedger";
import { hasPricedMoney } from "@/components/agent/lab/runs";
import { SessionKpis } from "@/components/agent/session/SessionKpis";
import { SessionOverview } from "@/components/agent/session/SessionOverview";
import { sessionPnlPoints } from "@/components/agent/session/pnlPoints";
import { OutsideWindow } from "@/components/agent/workspace/OutsideWindow";
import type { WorkspaceAlert } from "@/components/agent/workspace/views";
import { ReportViewer } from "@/components/routines/ReportViewer";
import { api, type AgentPerformance } from "@/lib/api";
import type { Decision, ParsedJournal } from "@/lib/parse-agent";

/**
 * What this run is and what it did, with nothing to click first (FEAT-119).
 *
 * Four blocks of one run — its money (the vitals, the report door and the
 * PnL curve, one card since ARCH-426), what wants a person, what it last
 * decided and what it put into the world — read top to bottom. They were split across two views (Now and the run overview) only
 * because a spine needed entries, and the split cost three bands twice over:
 * the deployment ledger, the canvas, and the last action, printed truncated to
 * one line in the vitals strip and whole six pixels below it. Merging them is
 * net-subtractive, which is the test for whether a consolidation is real.
 *
 * It still fetches almost nothing of its own. The vitals, the journal, the
 * chart's series and the ledger all come off `useRunReading`'s three
 * responses, which the tick spine and the detail bands were reading anyway; the
 * one query in here is the run's own report, which used to hang off the strip
 * in the overview and travels with it.
 */
export function NowView({
  slug,
  sslug,
  sessionNum,
  alerts,
  decisions,
  deployments,
  perf,
  journal,
  pnlSeries,
  onOpenTick,
  onShowOlderRuns,
  variant = "page",
}: {
  slug: string;
  sslug: string;
  /** The session in scope, or 0 when this strategy has no session run. */
  sessionNum: number;
  alerts: WorkspaceAlert[];
  /** The run's decisions, newest last. */
  decisions: Decision[];
  deployments: React.ComponentProps<typeof DeploymentLedger>["rows"];
  /** What the run's records are worth, for the vitals strip. */
  perf: AgentPerformance | null;
  /** The run's journal, for the chart's fallback series. */
  journal: ParsedJournal | null;
  pnlSeries?: { timestamp: string; pnl: number }[] | null;
  /** An alert, or the decision's own tick badge, is an address into a tick. */
  onOpenTick: (tick: number) => void;
  /**
   * Set when the strategy has sessions but none is in the loaded runs window
   * (CORR-376): the empty state then says so and widens the window, rather
   * than claiming the strategy never ran.
   */
  onShowOlderRuns?: () => void;
  /**
   * Where the run screen is drawn. In the chat's side panel the report covers
   * the pane only, as the tick overlay does, so the conversation stays visible.
   */
  variant?: "page" | "pane";
}) {
  const [showReport, setShowReport] = useState(false);
  const last = decisions[decisions.length - 1] ?? null;

  const { data: reportData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "report"],
    queryFn: () => api.getSessionReport(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });
  const report = reportData?.report ?? null;

  const priced = hasPricedMoney(perf);
  const metrics = journal?.metrics;
  const points = useMemo(
    () => (metrics ? sessionPnlPoints(metrics, pnlSeries) : []),
    [metrics, pnlSeries],
  );
  // Under two points there is no curve, only a dot — the honest answer for a
  // run one tick old is no chart at all.
  const hasChart = points.length > 1;

  // The session's own live report. It has always existed — rebuilt every tick
  // under a stable id — but was only reachable through the routines report
  // grid, where it appeared as a routine nobody had created.
  const reportDoor = report && (
    <button
      type="button"
      onClick={() => setShowReport(true)}
      className="flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1 text-[11px] font-medium normal-case tracking-normal text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
    >
      <FileText className="h-3 w-3" /> Session report
    </button>
  );

  return (
    <div className="space-y-4">
      {/* ① The run's money, in one card (ARCH-426): the report door in its
          header, the vitals, then the curve. The vitals appear only when there
          is priced money to put in them — a run that never traded reporting
          seven `+$0.00` tiles is the absence of a fact printed as a fact — and
          the curve only from two points up. */}
      {priced || hasChart ? (
        <section
          data-now-money
          className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
        >
          <header className="flex items-center justify-between gap-3 px-4 pt-3 pb-2">
            <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
              {hasChart && !pnlSeries?.length ? "PnL timeline" : "Realized PnL"}
            </h3>
            {reportDoor}
          </header>
          {priced && (
            <div className="px-4 pb-3">
              <SessionKpis perf={perf} />
            </div>
          )}
          {hasChart && (
            <div data-now-chart className="border-t border-[var(--color-border)]">
              <SessionOverview data={points} height={variant === "pane" ? 260 : 400} />
            </div>
          )}
        </section>
      ) : (
        reportDoor && (
          // The card is where the report lives, so a run with no money to put
          // in one would otherwise lose the only door to its own report.
          <div className="flex justify-end">{reportDoor}</div>
        )
      )}

      {/* ② What needs you. Derived from the run's own deeds and journal, not
          polled from anywhere: a failed action, a deploy the ledger never
          recorded, a tick that is late. The rules are pure, in `views.ts`. */}
      {alerts.length > 0 && (
        <div data-now-alerts className="space-y-2">
          {alerts.map((alert) => (
            <Alert key={alert.kind} alert={alert} onOpenTick={onOpenTick} />
          ))}
        </div>
      )}

      {/* ③ What it last decided, whole. Through the chat's own markdown
          renderer, because a model writes bold, lists and tables and the reader
          was getting the asterisks and the pipes. */}
      <div
        data-now-decision
        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
      >
        <h3 className="mb-3 flex flex-wrap items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <MessageSquareQuote className="h-3.5 w-3.5" /> Last decision
          {last && last.tick > 0 && (
            <button
              type="button"
              onClick={() => onOpenTick(last.tick)}
              className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
              title="Read the whole tick this came from"
            >
              #{last.tick}
            </button>
          )}
          {last?.time && (
            <span className="text-[10px] font-medium normal-case tracking-normal text-[var(--color-text-muted)]">
              {last.time}
            </span>
          )}
        </h3>
        {last ? (
          <>
            <div className="chat-markdown text-sm leading-relaxed text-[var(--color-text)]">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {last.action}
              </ReactMarkdown>
            </div>
            {last.reasoning && (
              <div className="chat-markdown mt-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {last.reasoning}
                </ReactMarkdown>
              </div>
            )}
            {last.riskNote && (
              <span className="mt-2 inline-block rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-400">
                {last.riskNote}
              </span>
            )}
          </>
        ) : sessionNum === 0 && onShowOlderRuns ? (
          <OutsideWindow onShowOlderRuns={onShowOlderRuns} />
        ) : (
          <p className="text-xs text-[var(--color-text-muted)]">
            {sessionNum > 0
              ? "This run has not decided anything yet."
              : "This strategy has not run yet."}
          </p>
        )}
      </div>

      {/* ④ What it put into the world (FEAT-100), read from the same response
          the vitals fold — so the two can never disagree. */}
      <DeploymentLedger
        rows={deployments}
        runKey={`${slug}.${sslug}`}
        sessionNum={sessionNum || undefined}
      />

      {showReport && report && (
        // `absolute` in the pane resolves against the run screen's `relative`
        // root, the same frame the tick overlay covers (CORR-422).
        <div
          data-now-report
          className={`${
            variant === "pane" ? "absolute" : "fixed"
          } inset-0 z-50 flex flex-col bg-[var(--color-bg)] p-4`}
        >
          <ReportViewer
            report={report}
            reports={[report]}
            onSelect={() => {}}
            onClose={() => setShowReport(false)}
            allowFullscreen={false}
          />
        </div>
      )}
    </div>
  );
}

function Alert({
  alert,
  onOpenTick,
}: {
  alert: WorkspaceAlert;
  onOpenTick: (tick: number) => void;
}) {
  const failed = alert.kind === "failed";
  const tick = alert.tick;
  return (
    <div
      data-alert={alert.kind}
      className={`flex items-start gap-2 rounded-lg border p-3 text-xs ${
        failed
          ? "border-red-500/30 bg-red-500/5 text-red-300"
          : "border-amber-500/30 bg-amber-500/5 text-amber-300"
      }`}
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 flex-1">{alert.text}</span>
      {tick !== undefined && (
        <button
          type="button"
          onClick={() => onOpenTick(tick)}
          className="shrink-0 rounded border border-[currentColor]/30 px-1.5 py-0.5 font-mono text-[10px] transition-opacity hover:opacity-80"
        >
          Open #{tick}
        </button>
      )}
    </div>
  );
}
