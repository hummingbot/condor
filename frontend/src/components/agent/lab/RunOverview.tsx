import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, Zap } from "lucide-react";
import { useMemo } from "react";

import {
  SessionActivity,
  SessionBots,
  SessionCanvasPanel,
  SessionExecutors,
  SnapshotBody,
} from "@/components/agent/AgentSessionContent";
import { SessionActions } from "@/components/agent/SessionActions";
import { api } from "@/lib/api";
import {
  type ParsedJournal,
  type ParsedSnapshot,
  parseJournal,
  parseSnapshot,
} from "@/lib/parse-agent";

/**
 * The evidence behind a run: what it ran, what it did, and what it said.
 *
 * Every band here is an existing export of `AgentSessionContent` — that is what
 * made the Lab tractable at all: `SessionReviewer` was a shell (a sidebar, a
 * sub-tab bar and a top bar) around bodies that were already written, so the
 * Lab replaced the shell and reused the bodies unchanged.
 *
 * It used to open with the run's *answers* too — the vitals strip, the
 * deployment ledger and the PnL chart — which is the same set the Now view led
 * with one spine entry away (FEAT-119). Those are the answer stack now, read
 * once, and what is left here is the evidence a reader opens when the answer is
 * not enough: the bots and executors it ran, the deeds it did, the narrative it
 * wrote and the questions it left itself.
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

  // Hoisted rather than reached through in the dependency list: the compiler
  // infers the whole `journalData` as the dependency and refuses to preserve a
  // memo whose declared dependency is narrower than what it inferred.
  const journalContent = journalData?.content;
  const journal = useMemo<ParsedJournal | null>(
    () => (journalContent ? parseJournal(journalContent) : null),
    [journalContent],
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

  const content = data?.content;
  const parsed = useMemo<ParsedSnapshot | null>(
    () => (content ? parseSnapshot(content) : null),
    [content],
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
