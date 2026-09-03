import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, FileText, Zap } from "lucide-react";
import { useMemo, useState } from "react";

import {
  SessionActivity,
  SessionBots,
  SessionCanvasPanel,
  SessionExecutors,
  SessionKpis,
  SessionOverview,
  SnapshotBody,
} from "@/components/agent/AgentSessionContent";
import { SessionActions } from "@/components/agent/SessionActions";
import { hasPricedMoney } from "@/components/agent/lab/runs";
import { ReportViewer } from "@/components/routines/ReportViewer";
import { api } from "@/lib/api";
import {
  type ParsedJournal,
  type ParsedSnapshot,
  parseJournal,
  parseSnapshot,
} from "@/lib/parse-agent";

/**
 * One run, read top to bottom.
 *
 * Every band here is an existing export of `AgentSessionContent` — that is what
 * made this feature tractable at all: `SessionReviewer` was a shell (a sidebar,
 * a sub-tab bar and a top bar) around bodies that were already written, so the
 * Lab replaces the shell and reuses the bodies unchanged.
 *
 * The one thing that is *not* carried over is the fake zero. The reviewer showed
 * its KPI strip unconditionally, so a run that never traded reported eight
 * `+$0.00` tiles — the absence of a fact printed as a fact. Here the strip
 * appears only when there is priced money to put in it.
 */
export function RunOverview({
  slug,
  sslug,
  sessionNum,
  serverName,
  controllerIds,
  isLiveSession,
  onSelectTick,
}: {
  slug: string;
  sslug: string;
  sessionNum: number;
  serverName: string;
  controllerIds?: string[];
  isLiveSession: boolean;
  /** A deed row or a chart marker is an address into a tick. */
  onSelectTick: (tick: number) => void;
}) {
  const [showReport, setShowReport] = useState(false);

  const { data: journalData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });

  const { data: perfData } = useQuery({
    queryKey: ["strategy-session-executors", slug, sslug, sessionNum],
    queryFn: () => api.getStrategySessionExecutors(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
    refetchInterval: 10000,
  });
  const perf = perfData?.performance ?? null;

  const { data: reportData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "report"],
    queryFn: () => api.getSessionReport(slug, sslug, sessionNum),
    enabled: sessionNum > 0,
  });
  const report = reportData?.report ?? null;

  const journal = useMemo<ParsedJournal | null>(
    () => (journalData?.content ? parseJournal(journalData.content) : null),
    [journalData?.content],
  );

  // A bot's controllers tag their executors with their own config id, never
  // with the agent_id, so streaming a bot-mode run needs the live bots' own
  // controller ids added to the caller's.
  const sessionControllerIds = useMemo(() => {
    const ids = new Set(controllerIds ?? []);
    const live = new Set(perf?.bot_names ?? []);
    for (const c of perf?.controllers ?? []) {
      if (c.controller_id && live.has(c.bot_name)) ids.add(c.controller_id);
    }
    return Array.from(ids);
  }, [controllerIds, perf?.bot_names, perf?.controllers]);

  return (
    <div className="space-y-4">
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
      {journal && (
        <SessionOverview
          journal={journal}
          perf={perf}
          pnlSeries={perfData?.pnl_series}
        />
      )}
      <SessionBots perf={perf} />
      <SessionExecutors
        slug={slug}
        sslug={sslug}
        sessionNum={sessionNum}
        serverName={serverName}
        controllerIds={sessionControllerIds}
        onSnapshotClick={onSelectTick}
        isLiveSession={isLiveSession}
        botMode={(perf?.bot_instances?.length ?? 0) > 0}
      />
      <SessionCanvasPanel slug={slug} sslug={sslug} sessionNum={sessionNum} />
      {/* What it did, then what it said about it (FEAT-097). */}
      <Band icon={Zap} label="Actions">
        <SessionActions
          slug={slug}
          sslug={sslug}
          sessionNum={sessionNum}
          onSnapshotClick={onSelectTick}
        />
      </Band>
      {journal && (
        <Band icon={Activity} label="Decisions">
          <SessionActivity journal={journal} />
        </Band>
      )}

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

function Band({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof Zap;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="mb-2 flex items-center gap-2 px-1 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        <Icon className="h-3.5 w-3.5" /> {label}
      </h3>
      {children}
    </div>
  );
}

/**
 * A dry run or a single tick — one file, one tick, no journal.
 *
 * It is the same document a session snapshot is (`parseSnapshot` reads both), so
 * it renders through the same body rather than through a second copy of it. The
 * one thing added is the error banner: a tick whose model call failed writes the
 * raw error as its Agent Response, and that is the only outcome a dry run has.
 */
export function ExperimentDetail({
  slug,
  sslug,
  number,
}: {
  slug: string;
  sslug: string;
  number: number;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["strategy", slug, sslug, "experiment", number],
    queryFn: () => api.getExperiment(slug, sslug, number),
    enabled: number > 0,
  });

  const parsed = useMemo<ParsedSnapshot | null>(
    () => (data?.content ? parseSnapshot(data.content) : null),
    [data?.content],
  );

  if (isLoading || !parsed) {
    return (
      <div className="flex h-48 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const failed = isErrorResponse(parsed.agentResponse);

  return (
    <div className="space-y-4">
      {failed && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4">
          <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-red-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            Agent Error
          </h3>
          <div className="whitespace-pre-wrap font-mono text-xs text-red-300">
            {parsed.agentResponse}
          </div>
        </div>
      )}
      <SnapshotBody parsed={parsed} />
    </div>
  );
}

/**
 * A tick whose model call failed writes the raw error string as its Agent
 * Response (e.g. `(error: status_code: 404, ...)`).
 */
function isErrorResponse(text: string): boolean {
  const t = text.trimStart();
  return /^\(?error\b/i.test(t) || /\berror: status_code:/i.test(t);
}
