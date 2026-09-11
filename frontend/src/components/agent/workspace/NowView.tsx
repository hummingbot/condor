import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, FileText, MessageSquareQuote } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  SessionKpis,
  SessionOverview,
} from "@/components/agent/AgentSessionContent";
import { DeploymentLedger } from "@/components/agent/lab/DeploymentLedger";
import { hasPricedMoney } from "@/components/agent/lab/runs";
import type { WorkspaceAlert } from "@/components/agent/workspace/views";
import { ReportViewer } from "@/components/routines/ReportViewer";
import { api, type AgentPerformance } from "@/lib/api";
import type { Decision, ParsedJournal } from "@/lib/parse-agent";

/**
 * What this run is and what it did, with nothing to click first (FEAT-119).
 *
 * Five facets of one run — its vitals, what wants a person, what it last
 * decided, what it has earned and what it put into the world — read top to
 * bottom. They were split across two views (Now and the run overview) only
 * because a spine needed entries, and the split cost three bands twice over:
 * the deployment ledger, the canvas, and the last action, printed truncated to
 * one line in the vitals strip and whole six pixels below it. Merging them is
 * net-subtractive, which is the test for whether a consolidation is real.
 *
 * It still fetches almost nothing of its own. The vitals, the journal, the
 * chart's series and the ledger all come off `useWorkspaceAlerts`' three
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
  /** The run's journal, for the strip's status and the chart's fallback. */
  journal: ParsedJournal | null;
  pnlSeries?: { timestamp: string; pnl: number }[] | null;
  /** An alert, or the decision's own tick badge, is an address into a tick. */
  onOpenTick: (tick: number) => void;
}) {
  const [showReport, setShowReport] = useState(false);
  const last = decisions[decisions.length - 1] ?? null;

  const { data: reportData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "report"],
    queryFn: () => api.getSessionReport(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });
  const report = reportData?.report ?? null;

  return (
    <div className="space-y-4">
      {/* ① The run's vitals. The strip appears only when there is priced money
          to put in it: a run that never traded reporting eight `+$0.00` tiles
          is the absence of a fact printed as a fact. */}
      {hasPricedMoney(perf) ? (
        <SessionKpis
          perf={perf}
          summary={journal?.summary}
          hasReport={!!report}
          onOpenReport={() => setShowReport(true)}
        />
      ) : (
        report && (
          // The strip is where the report lives, so a run with no money to put
          // in a strip would otherwise lose the only door to its own report.
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowReport(true)}
              className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
            >
              <FileText className="h-3 w-3" /> Session report
            </button>
          </div>
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
        ) : (
          <p className="text-xs text-[var(--color-text-muted)]">
            {sessionNum > 0
              ? "This run has not decided anything yet."
              : "This strategy has not run yet."}
          </p>
        )}
      </div>

      {/* ④ What it has earned over the run. `SessionOverview` is the chart and
          nothing else, and it draws nothing at all under two points — which is
          the honest answer for a run one tick old. */}
      {journal && (
        <SessionOverview journal={journal} perf={perf} pnlSeries={pnlSeries} />
      )}

      {/* ⑤ What it put into the world (FEAT-100), read from the same response
          the vitals fold — so the two can never disagree. */}
      <DeploymentLedger
        rows={deployments}
        runKey={`${slug}.${sslug}`}
        sessionNum={sessionNum || undefined}
      />

      {showReport && report && (
        <div className="fixed inset-0 z-50 flex flex-col bg-[var(--color-bg)] p-4">
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
