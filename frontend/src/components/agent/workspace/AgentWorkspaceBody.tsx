import { useQuery } from "@tanstack/react-query";
import { ExternalLink, ScrollText } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AgentKnowledge } from "@/components/agent/AgentKnowledge";
import { DelegationSheet } from "@/components/agent/DelegationSheet";
import { SnapshotDetail } from "@/components/agent/AgentSessionContent";
import { ExperimentDetail, RunOverview } from "@/components/agent/lab/RunOverview";
import { RunRail } from "@/components/agent/lab/RunRail";
import { StrategyWorkbench } from "@/components/agent/StrategyWorkbench";
import { isLoopRun } from "@/components/agent/lab/runs";
import { isKnowledgeTab } from "@/components/agent/knowledgeTabs";
import { AgentFleet } from "@/components/agent/workspace/AgentFleet";
import { MoneyView } from "@/components/agent/workspace/MoneyView";
import { LoopBar } from "@/components/agent/workspace/LoopBar";
import { NowView } from "@/components/agent/workspace/NowView";
import { useWorkspaceAlerts } from "@/components/agent/workspace/useWorkspaceAlerts";
import { WorkspaceSpine } from "@/components/agent/workspace/WorkspaceSpine";
import {
  pickRun,
  pickStrategy,
  spineSectionFor,
  type WorkspaceViewId,
} from "@/components/agent/workspace/views";
import type { WorkspaceUrlAdapter } from "@/components/agent/workspace/workspaceUrl";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import {
  api,
  type AgentRunRow,
  type DelegationStatus,
  type DelegationSummary,
  type StrategyDetail,
} from "@/lib/api";

/**
 * How many runs one page of the rail holds (FEAT-111).
 *
 * The rail lists four kinds now, and conversations are the unbounded one: a
 * year of chatting is hundreds of rows, and a five-second poll that pulled all
 * of them would undo the cheapness that licenses the poll in the first place.
 */
const RUN_PAGE = 100;

/** Statuses `DELEGATION_STATUS` can colour; anything else is `unknown`. */
const DELEGATION_STATUSES: readonly DelegationStatus[] = [
  "running",
  "done",
  "error",
  "stopped",
  "interrupted",
  "timeout",
  "unknown",
];

/**
 * A rail row, as the delegation sheet reads a history row.
 *
 * The row carries the record's *listing* fields and none of its bodies, which
 * is exactly the shape `DelegationSheet` was built to open — it fetches the
 * record itself when the caller has no body, and that is how a task recorded by
 * a long-dead process is still readable. The status is narrowed rather than
 * cast: a record written by a newer build could name a state this dashboard
 * cannot colour, and `unknown` is the honest cell for it.
 */
function delegationTask(run: AgentRunRow, agent: string): DelegationSummary {
  const status = DELEGATION_STATUSES.find((s) => s === run.status) ?? "unknown";
  return {
    task_id: run.id,
    agent,
    user_id: 0,
    chat_id: 0,
    server_name: null,
    task: run.title,
    status,
    kind: run.execution_mode === "consult" ? "consult" : "delegate",
    conversation_id: "",
    started_at: run.started_at ?? 0,
    ended_at: run.ended_at ?? 0,
  };
}

/**
 * One agent's whole workspace, with no opinion about where it is drawn
 * (FEAT-117).
 *
 * The loop bar, the spine and every body behind it — the five **Doing** views
 * and the seven **Being** sections — with the URL reading and writing handed in
 * as an {@link WorkspaceUrlAdapter} rather than reached for. That last part is
 * the whole extraction: `pages/AgentWorkspace.tsx` used to read `useParams()`
 * for the slug and `useSearchParams()` for everything else, which is exactly
 * what made it unmountable anywhere but at `/agents/:slug`.
 *
 * Two hosts now. The page supplies the slug from its path, its own
 * `useSearchParams` and a `<WorkspaceHeader/>`; the chat's pane supplies the
 * slug from `?who=`, the *home's* `useSearchParams` and no header at all —
 * the sheet's bar is its header. Neither one is visible from in here.
 *
 * This is the third shell in the same family and the last: `PerfBrowser` was
 * made host-agnostic so the workspace could mount the fleet browser
 * (FEAT-108), `AgentKnowledge` so the chat could mount the page's sections
 * (FEAT-081). Every body below is imported unchanged; what moved is the shell.
 */
export function AgentWorkspaceBody({
  slug,
  adapter,
  header,
  onAskAgent,
  onOpenRoutine,
  onDirtyChange,
}: {
  slug: string;
  /** Where this workspace's four parameters are read and written. */
  adapter: WorkspaceUrlAdapter;
  /**
   * Above the loop bar. The page passes `<WorkspaceHeader/>`; the pane passes
   * nothing, because a sheet already has a bar with the agent's name in it and
   * a second one under it is the same fact twice.
   *
   * A function rather than a node, as `WorkspaceSheet`'s own `header` is, and
   * for the same reason: the page's header carries the loop's start/pause/stop
   * controls, which act on the strategy *this* component resolved from the URL.
   * Handing it in as a node would mean the host resolving the scope a second
   * time, and the two answers drifting the first time the rule changed.
   */
  header?: (state: { strategy: StrategyDetail | null }) => React.ReactNode;
  /**
   * Put a request from a knowledge row to this agent (FEAT-092).
   *
   * The hosts have different powers, which is why this is a prop and not a
   * `navigate` in here: the page can only navigate to a conversation, the chat
   * sends into one it already has open.
   */
  onAskAgent: (text: string) => void;
  /**
   * Where a routine row goes. The chat's pane hands the pane to the routine
   * library it already houses (FEAT-077); the page has no such surface and
   * passes nothing, which leaves the row a plain list entry.
   */
  onOpenRoutine?: (routineName: string) => void;
  /**
   * An editor in the Being sections has unsaved text. A host that can be closed
   * in one click owes the reader a question before dropping it; the page, which
   * has to be navigated away from, passes nothing.
   */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const navigate = useNavigate();
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);

  const { url, set: setParams } = adapter;

  const selectView = useCallback(
    (view: WorkspaceViewId) => setParams({ view }),
    [setParams],
  );

  /**
   * A beat is a selection, not a destination.
   *
   * Clicking one sets `?tick=` and swaps the body to that tick without touching
   * the route — which is the whole argument for the query-parameter grammar.
   * Clearing it puts back the run it belongs to, so the spine's `Run` button is
   * the way up from a tick to its overview.
   */
  const selectTick = useCallback(
    (tick: number | null, from: WorkspaceViewId) =>
      setParams({
        tick,
        view: tick === null ? (from === "tick" ? "runs" : from) : "tick",
      }),
    [setParams],
  );

  // One `["agent", slug]` and one `["agent-runs", slug]` for the whole screen.
  // The header, the loop bar and the body all want them; react-query dedupes
  // the keys, which is the only reason three regions polling at 5s is one poll
  // — and the reason the page can hold its own `["agent", slug]` for the header
  // without buying a second one.
  const { data: agent, isLoading } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  // The rail's window, not a filter (FEAT-111). An install that has been
  // chatted with for a year has hundreds of conversations, and pulling the
  // archive on a five-second poll is how a cheap rail stops being cheap. The
  // window widens on request and stays widened for the visit.
  const [runLimit, setRunLimit] = useState(RUN_PAGE);
  const { data: runs = [] } = useQuery({
    queryKey: ["agent-runs", slug, runLimit],
    queryFn: () => api.getAgentRuns(slug, runLimit),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  // Hoisted rather than reached through in the dependency lists: the compiler
  // infers the whole `agent` as the dependency and refuses to preserve a memo
  // whose declared one is narrower.
  const strategies = agent?.strategies;
  const sslug = useMemo(
    () => pickStrategy(strategies ?? [], runs, url.strategy),
    [strategies, runs, url.strategy],
  );
  const selectedRun = useMemo(
    () => pickRun(runs, sslug, url.run),
    [runs, sslug, url.run],
  );

  // The loop bar's picker is a loop concept end to end — it names ticks and a
  // cadence — so it is handed the loop's runs only. A chat in that dropdown
  // would offer a run whose every other control is inert.
  const scopedRuns = useMemo(
    () =>
      runs.filter(
        (r) => isLoopRun(r.kind) && (!sslug || r.strategy_slug === sslug),
      ),
    [runs, sslug],
  );

  const { data: strategy = null } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug!),
    enabled: !!slug && !!sslug,
    refetchInterval: 5000,
  });

  // The live engine behind the selected run, for the cadence and the countdown
  // — the two facts a run row deliberately does not carry.
  const instances = strategy?.instances;
  const runAgentId = selectedRun?.agent_id;
  const instance = useMemo(
    () => instances?.find((i) => i.agent_id === runAgentId) ?? null,
    [instances, runAgentId],
  );

  // What this run wants a person for. Read at this level rather than inside
  // Now, because the spine carries the count on every view — an alert you have
  // to open a section to discover is not one — and the three queries behind it
  // are the tick spine's and the run overview's own, so they cost one round.
  const { alerts, decisions, deployments, sessionNum } = useWorkspaceAlerts({
    slug,
    sslug,
    run: selectedRun,
    instance,
  });

  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  /**
   * Opening a run, which is now four different things (FEAT-111).
   *
   * A loop run and a delegation are *selections*: the rail stays, the body
   * changes, and `?run=` says which. A conversation is not — the chat is the
   * surface for a conversation, and rebuilding a wide surface inside a narrow
   * one is exactly what FEAT-103's alternative D argued against. So a chat row
   * navigates to the chat, carrying the conversation it wants opened.
   */
  const openRun = useCallback(
    (run: AgentRunRow) => {
      if (run.kind === "conversation") {
        navigate(`/?conversation=${encodeURIComponent(run.id)}`);
        return;
      }
      setParams(
        isLoopRun(run.kind)
          ? { strategy: run.strategy_slug, run: run.run_id }
          : { run: run.run_id },
      );
    },
    [navigate, setParams],
  );

  // The page has already guarded this by the time it mounts the body — the
  // query is shared and warm — so this is the pane's spinner, where a `?who=`
  // can name an agent nothing has fetched yet.
  if (isLoading || !agent) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const view = url.view;
  // Where this agent's work actually happens: the strategy's own configured
  // server, else the agent's pin. `/bots` reads the ambient server and the
  // fleet map deliberately does not, so a rooted fleet has to read the agent's
  // (FEAT-108) — otherwise an agent trading on another server has a Fleet view
  // that cannot fetch its own bots.
  const strategyServer =
    (strategy?.config?.server_name as string) || agent.server_name || "";

  const body = isKnowledgeTab(view) ? (
    /* Keyed on the section so a playbook left open in Skills does not follow
       the reader into Memories — the reset `AgentKnowledge`'s own tab click
       does, for a host that clicks nothing. */
    <AgentKnowledge
      key={view}
      slug={agent.slug}
      tab={view}
      onAskAgent={onAskAgent}
      onOpenRoutine={onOpenRoutine}
      onDirtyChange={onDirtyChange}
      routinesAction={
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            onClick={() => setShowRoutinesBrowser(true)}
            className="flex shrink-0 items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
            title="Every run these routines produced, and their reports"
          >
            <ScrollText className="h-3 w-3" /> Reports
          </button>
          <Link
            to={`/routines?agent=${agent.slug}`}
            className="flex shrink-0 items-center gap-1 px-1 py-1 text-[11px] text-[var(--color-text-muted)]/70 transition-colors hover:text-[var(--color-text)]"
            title="The full library, on its own page"
          >
            <ExternalLink className="h-3 w-3" /> Full library
          </Link>
        </div>
      }
      /* A strategy card is a *scope change*, not a door: the workbench is one
         of this spine's own views, so the card moves `?view=` and `?strategy=`
         in place. Both hosts do the same thing, which is what stopped the pane
         from needing a strategy sheet of its own. */
      onOpenStrategy={(strategySlug) =>
        setParams({ view: "playbook", strategy: strategySlug })
      }
    />
  ) : !sslug ? (
    <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
      This agent has no strategies yet, so there is no loop to look at.
    </p>
  ) : view === "money" ? (
    /* Two numbers, named apart and reconciled (FEAT-109). This used to be the
       run rollup alone, which is a different quantity from the one the fleet
       page prints at the same scope — and shown as the only number, the reader
       had no way to know that neither was broken. */
    <MoneyView
      slug={agent.slug}
      sslug={sslug}
      strategy={url.strategy}
      strategies={agent.strategies ?? []}
      serverName={strategyServer}
    />
  ) : view === "tick" ? (
    /* One tick of one run, the same body the Lab renders — reached by clicking
       a beat on the spine above, which is why this view has no picker of its
       own. A dry run is a single tick in one file and has no beats. */
    selectedRun && selectedRun.kind === "session" && url.tick !== null ? (
      <SnapshotDetail
        slug={agent.slug}
        sslug={sslug}
        sessionNum={selectedRun.number}
        tick={url.tick}
      />
    ) : (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        Pick a beat on the spine above to read the tick it wrote.
      </p>
    )
  ) : view === "fleet" ? (
    <AgentFleet
      slug={agent.slug}
      sslug={sslug}
      serverName={strategyServer}
      run={selectedRun}
    />
  ) : view === "runs" ? (
    <RunsView
      slug={agent.slug}
      runs={runs}
      strategyFilter={url.strategy}
      onStrategyFilter={(next) => setParams({ strategy: next })}
      selected={selectedRun}
      onSelectRun={openRun}
      tick={url.tick}
      onSelectTick={(next) => selectTick(next, "runs")}
      serverName={strategyServer}
      controllerIds={instance ? [instance.agent_id] : undefined}
      hasMore={runs.length >= runLimit}
      onShowMore={() => setRunLimit((n) => n + RUN_PAGE)}
      onClearRun={() => setParams({ run: null })}
    />
  ) : view === "now" ? (
    <NowView
      slug={agent.slug}
      sslug={sslug}
      sessionNum={sessionNum}
      alerts={alerts}
      decisions={decisions}
      deployments={deployments}
      instance={instance}
      onOpenTick={(next) => selectTick(next, "now")}
    />
  ) : (
    /* The Runs band comes off here — the spine's Runs entry is the door now,
       and a band that duplicates it inside the body is the second door this
       feature exists to remove. */
    <StrategyWorkbench
      slug={agent.slug}
      sslug={sslug}
      showRuns={false}
      onDeleted={() => setParams({ view: "strategies", strategy: null })}
    />
  );

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      {header?.({ strategy })}

      <LoopBar
        slug={agent.slug}
        strategies={agent.strategies ?? []}
        sslug={sslug}
        onSelectStrategy={(next) => setParams({ strategy: next })}
        runs={scopedRuns}
        run={selectedRun && isLoopRun(selectedRun.kind) ? selectedRun : null}
        onSelectRun={(runId) => setParams({ run: runId })}
        instance={instance}
        tick={url.tick}
        onSelectTick={(next) => selectTick(next, view)}
      />

      <div className="flex min-h-0 flex-1">
        <WorkspaceSpine
          current={spineSectionFor(view)}
          onSelect={selectView}
          alertCount={alerts.length}
        />
        <div
          className={`min-w-0 flex-1 ${
            view === "runs" || view === "fleet" ? "flex min-h-0" : "overflow-y-auto p-4"
          }`}
        >
          {body}
        </div>
      </div>

      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}
    </div>
  );
}

/**
 * Every run of every strategy, and every tick of one — the Lab's whole job.
 *
 * The rail and the overview are the Lab's own bodies, imported unchanged: what
 * the Lab was, minus the page around it. Its header is gone because the loop bar
 * above already says which run this is and whether it is still going, which the
 * Lab could not — it had no agent name in it at all.
 */
function RunsView({
  slug,
  runs,
  strategyFilter,
  onStrategyFilter,
  selected,
  onSelectRun,
  tick,
  onSelectTick,
  serverName,
  controllerIds,
  hasMore,
  onShowMore,
  onClearRun,
}: {
  slug: string;
  runs: AgentRunRow[];
  strategyFilter: string | null;
  onStrategyFilter: (sslug: string | null) => void;
  selected: AgentRunRow | null;
  onSelectRun: (run: AgentRunRow) => void;
  tick: number | null;
  onSelectTick: (tick: number | null) => void;
  serverName: string;
  controllerIds?: string[];
  hasMore?: boolean;
  onShowMore?: () => void;
  /** Put the selection back to the newest run — the sheet's way out. */
  onClearRun: () => void;
}) {
  return (
    <>
      <RunRail
        runs={runs}
        strategyFilter={strategyFilter}
        onStrategyFilter={onStrategyFilter}
        selectedKey={
          selected ? `${selected.strategy_slug}:${selected.run_id}` : null
        }
        onSelectRun={onSelectRun}
        isLoading={false}
        hasMore={hasMore}
        onShowMore={onShowMore}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {!selected ? (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
            This agent has no runs yet.
          </p>
        ) : selected.kind === "delegation" ? (
          /* The one place a background task is read already exists — the dock,
             the fleet card and an agent's history all open this sheet — and a
             rail row is the fourth caller, not a fourth copy. */
          <DelegationSheet
            task={delegationTask(selected, slug)}
            onClose={onClearRun}
          />
        ) : selected.kind === "conversation" ? (
          /* Only reachable from a hand-typed `?run=c:…`: a chat row navigates
             to the chat rather than selecting. The rail still highlights it,
             so the body says where its transcript is instead of nothing. */
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
            {selected.title || "This chat"} is read in the chat.
          </p>
        ) : selected.kind === "experiment" ? (
          <ExperimentDetail
            slug={slug}
            sslug={selected.strategy_slug}
            number={selected.number}
          />
        ) : tick !== null ? (
          /* A `?tick=` on the runs view is the Lab's own address, and the
             redirect from `/agents/:slug/runs` lands on it with its query
             string intact — so it has to read the tick, not the overview. */
          <SnapshotDetail
            slug={slug}
            sslug={selected.strategy_slug}
            sessionNum={selected.number}
            tick={tick}
          />
        ) : (
          <RunOverview
            slug={slug}
            sslug={selected.strategy_slug}
            sessionNum={selected.number}
            serverName={serverName}
            controllerIds={controllerIds}
            isLiveSession={
              selected.status === "running" || selected.status === "paused"
            }
            onSelectTick={onSelectTick}
          />
        )}
      </div>
    </>
  );
}
