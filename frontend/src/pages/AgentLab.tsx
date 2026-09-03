import { useQuery } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import { useCallback, useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { RunOverview, ExperimentDetail } from "@/components/agent/lab/RunOverview";
import { RunRail } from "@/components/agent/lab/RunRail";
import { TickSpine } from "@/components/agent/lab/TickSpine";
import {
  formatDuration,
  isLiveRun,
  parseRunId,
  runDurationSec,
  runLabel,
} from "@/components/agent/lab/runs";
import { SnapshotDetail } from "@/components/agent/AgentSessionContent";
import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import { api, type AgentRunRow } from "@/lib/api";

/**
 * What an agent actually did, run by run and tick by tick.
 *
 * Every agent surface before this one was keyed on the **strategy** — a playbook
 * plus a config — and reported it as money. But the unit of a loop agent is the
 * *run*: `sessions/session_N/` or `dry_runs/experiment_M.md`, and a run is a
 * sequence of ticks. Money is downstream of that, and for most runs it is zero,
 * so every surface showed nothing: two cards, eight stats, every one `+$0.00`,
 * an entire dry run reduced to a `+1🧪` superscript, and twenty snapshots three
 * clicks deep inside an overlay with no URL at all.
 *
 * So this is the one surface for run analysis, and `SessionReviewer` and the
 * sessions table are gone rather than kept beside it. The split with the
 * strategy workbench is: **the workbench operates a strategy, the Lab reads its
 * runs.**
 *
 * The URL is the whole state — `?strategy=…&run=s3&tick=7` — which is the point
 * of moving off an overlay: a tick can be linked, bookmarked and pasted into a
 * new tab. `run` absent means the newest run of the selected strategy; `tick`
 * absent means the run overview.
 *
 * A thin page over a browser, the same shape `/routines` is over `ReportBrowser`
 * and `/bots` is over `PerfBrowser`.
 */
export function AgentLab() {
  const { slug = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();

  const strategyParam = searchParams.get("strategy");
  const runParam = searchParams.get("run");
  const tickParam = searchParams.get("tick");
  const tick = tickParam && /^\d+$/.test(tickParam) ? Number(tickParam) : null;

  // Disk only on the server side, so it polls like the fleet map does.
  const { data: runs = [], isLoading } = useQuery({
    queryKey: ["agent-runs", slug],
    queryFn: () => api.getAgentRuns(slug),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  const scoped = useMemo(
    () =>
      strategyParam ? runs.filter((r) => r.strategy_slug === strategyParam) : runs,
    [runs, strategyParam],
  );

  // `run` absent — or naming a run that is not in the current scope — falls back
  // to the newest one, which is what a bare `/agents/x/runs` should open on.
  const selected: AgentRunRow | null = useMemo(() => {
    const ref = parseRunId(runParam);
    if (ref) {
      const match = scoped.find(
        (r) => r.kind === ref.kind && r.number === ref.number,
      );
      if (match) return match;
    }
    return scoped[0] ?? null;
  }, [scoped, runParam]);

  const navigateTo = useCallback(
    (next: {
      strategy?: string | null;
      run?: string | null;
      tick?: number | null;
    }) => {
      const params = new URLSearchParams(searchParams);
      for (const [key, value] of Object.entries(next)) {
        if (value === null || value === undefined || value === "") params.delete(key);
        else params.set(key, String(value));
      }
      setSearchParams(params);
    },
    [searchParams, setSearchParams],
  );

  const selectRun = useCallback(
    (run: AgentRunRow) => {
      // The strategy rides along even when the rail is showing all of them: a
      // pasted URL has to resolve to one run without depending on what the
      // reader had filtered at the time.
      navigateTo({ strategy: run.strategy_slug, run: run.run_id, tick: null });
    },
    [navigateTo],
  );

  return (
    <div className="flex h-full min-h-0 w-full">
      <RunRail
        runs={runs}
        strategyFilter={strategyParam}
        onStrategyFilter={(sslug) => navigateTo({ strategy: sslug, run: null, tick: null })}
        selectedKey={selected ? `${selected.strategy_slug}:${selected.run_id}` : null}
        onSelectRun={selectRun}
        isLoading={isLoading}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <RunBody
            slug={slug}
            run={selected}
            tick={tick}
            onSelectTick={(next) => navigateTo({ tick: next })}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-[var(--color-text-muted)]">
            {isLoading ? "Loading runs…" : "This agent has no runs yet."}
          </div>
        )}
      </div>
    </div>
  );
}

/** The selected run: its header, its spine, and whichever body the URL asks for. */
function RunBody({
  slug,
  run,
  tick,
  onSelectTick,
}: {
  slug: string;
  run: AgentRunRow;
  tick: number | null;
  onSelectTick: (tick: number | null) => void;
}) {
  const sslug = run.strategy_slug;
  const live = isLiveRun(run);

  // The strategy's own detail, for the two things a run row deliberately does
  // not carry: which server its executors stream from, and the live engine.
  const { data: strategy } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug),
    enabled: !!slug && !!sslug,
    refetchInterval: live ? 5000 : false,
  });

  const now = useSeconds(live);
  const serverName = (strategy?.config?.server_name as string) || "";
  const instance =
    strategy?.instances?.find((i) => i.agent_id === run.agent_id) ?? null;
  const controllerIds = instance ? [instance.agent_id] : [];

  const dueIn =
    instance && instance.last_tick_at > 0
      ? instance.last_tick_at + instance.frequency_sec - now / 1000
      : null;

  const duration = formatDuration(runDurationSec(run, now / 1000));

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--color-border)] px-4 py-2.5 text-xs">
        <span className="font-mono text-sm font-bold text-[var(--color-text)]">
          {runLabel(run)}
        </span>
        <Link
          to={`/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}`}
          className="flex items-center gap-1 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
          title="Operate this strategy — start, stop, playbook"
        >
          <SlidersHorizontal className="h-3 w-3" />
          {run.strategy_name}
        </Link>
        <span className="opacity-40">·</span>
        <span
          className={
            live
              ? "text-emerald-400"
              : run.error
                ? "text-[var(--color-red)]"
                : "text-[var(--color-text-muted)]"
          }
        >
          {run.status}
        </span>
        <span className="opacity-40">·</span>
        <span className="font-mono text-[var(--color-text-muted)]">
          {run.tick_count} tick{run.tick_count === 1 ? "" : "s"}
        </span>
        {duration && (
          <>
            <span className="opacity-40">·</span>
            <span className="font-mono text-[var(--color-text-muted)]">{duration}</span>
          </>
        )}
        {dueIn !== null && (
          <>
            <span className="opacity-40">·</span>
            <span className="font-mono text-[var(--color-text-muted)]">
              {dueIn > 0 ? `next in ${countdown(dueIn)}` : `overdue ${countdown(-dueIn)}`}
            </span>
          </>
        )}
      </div>

      {run.kind === "session" && (
        <TickSpine
          slug={slug}
          sslug={sslug}
          sessionNum={run.number}
          hasActionsLog={run.has_actions_log}
          selectedTick={tick}
          onSelectTick={onSelectTick}
        />
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {run.kind === "experiment" ? (
          <ExperimentDetail slug={slug} sslug={sslug} number={run.number} />
        ) : tick !== null ? (
          <SnapshotDetail
            slug={slug}
            sslug={sslug}
            sessionNum={run.number}
            tick={tick}
          />
        ) : (
          <RunOverview
            slug={slug}
            sslug={sslug}
            sessionNum={run.number}
            serverName={serverName}
            controllerIds={controllerIds}
            isLiveSession={live}
            onSelectTick={onSelectTick}
          />
        )}
      </div>
    </>
  );
}
