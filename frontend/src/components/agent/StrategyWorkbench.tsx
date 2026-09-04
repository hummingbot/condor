import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ChevronRight, FileText, FlaskConical, Layers, Repeat, ScrollText, Trash2, X, Zap } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import { AgentMarketStrip } from "@/components/agent/AgentMarketStrip";
import {
  InstanceCard,
  MarkdownEditor,
  PerformancePanel,
} from "@/components/agent/AgentOverviewTab";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { DeployedFleet } from "@/components/agent/DeployedFleet";
import { LoopPulse } from "@/components/agent/LoopPulse";
import { isLiveRun, runFacts, runLabel } from "@/components/agent/lab/runs";
import { DiscardChangesDialog } from "@/components/editor/EditorDialogs";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useSeconds } from "@/hooks/useSeconds";
import { api, type AgentRunRow } from "@/lib/api";
import { groupExecutorsByMarket } from "@/lib/executor-overlays";

/**
 * A strategy, everywhere a strategy is shown.
 *
 * This was the body of `pages/StrategyDetail`, and only ever that: opening a
 * strategy meant leaving whatever you were doing for a page of its own. Beside
 * a conversation that is the wrong trade — you click a card the agent just told
 * you about and lose the agent — so the body moved here and the page became a
 * thin host for it, alongside a second host in the chat's workspace pane
 * (`StrategySheet`). One component, so the pane can never be the page's poorer
 * cousin: whatever you can do to a strategy, you can do wherever it is open.
 *
 * `dense` is the pane's half-row rather than a page's full width. It is a prop
 * and not a media query for the reason `AgentStrategies.dense` is: the *window*
 * is wide in both cases, so `lg:` is true in a 640px column and an eight-across
 * stat grid lands there as eight slivers.
 *
 * The playbook editor, the routine browser and the delete confirmation stay
 * overlays here, above whichever host is on screen. They are modal work — you
 * are editing one file, or confirming one destruction — and an overlay inside a
 * pane that is itself beside a chat is still the smallest thing that can hold
 * them.
 *
 * Run *analysis* is not here any more (FEAT-099). The session reviewer was an
 * overlay with no URL of its own, so the one surface where you read what a loop
 * actually did could not be linked, bookmarked or shared. It lives at
 * `/agents/:slug?open=runs` now, and the split is clean: **the workbench
 * operates a strategy, the run screen reads its runs.** What stays here is what
 * this component is good at — identity, start/stop/pause, live executor charts,
 * playbook and learnings, delete.
 *
 * `showRuns` is off in the agent's run screen and on in the chat's pane
 * (FEAT-103). The band is a door to the runs, and on that screen the Runs
 * disclosure already is one, four bands up; in a pane beside a conversation
 * there is no spine, so the band is still the only way in.
 */
export function StrategyWorkbench({
  slug,
  sslug,
  dense = false,
  showRuns = true,
  onDeleted,
}: {
  slug: string;
  sslug: string;
  /** Half a workspace row rather than a page: one column, tighter grids. */
  dense?: boolean;
  /** The Runs band — off for a host whose own navigation already has one. */
  showRuns?: boolean;
  /** The host's move after a delete — pop the pane, or navigate the page. */
  onDeleted: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showStrategyModal, setShowStrategyModal] = useState(false);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  // Unsaved-edit guards for the Playbook/Learnings editors (CORR-093)
  const [playbookDirty, setPlaybookDirty] = useState(false);
  const [learningsDirty, setLearningsDirty] = useState(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);

  // "Resume after restart", written straight from the loop's own spine. It
  // invalidates the strategy rather than tracking the answer locally, so the
  // chip reflects what is actually on disk and a failed write snaps back
  // instead of leaving the reader believing a loop is armed when it is not.
  const restartMutation = useMutation({
    mutationFn: (enabled: boolean) => api.setRestartOnBoot(slug, sslug, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug, sslug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
      onDeleted();
    },
  });

  // Close the strategy modal, dropping any unsaved-edit guards.
  const closeStrategyModal = useCallback(() => {
    setShowStrategyModal(false);
    setShowDiscardConfirm(false);
    setPlaybookDirty(false);
    setLearningsDirty(false);
  }, []);

  // Backdrop click, Escape and the X button all route through here: with
  // unsaved edits they ask for confirmation instead of silently discarding.
  const requestCloseStrategyModal = useCallback(() => {
    if (playbookDirty || learningsDirty) {
      setShowDiscardConfirm(true);
    } else {
      closeStrategyModal();
    }
  }, [playbookDirty, learningsDirty, closeStrategyModal]);

  // Close strategy modal on Escape (the discard dialog owns Escape while open)
  useEscapeKey(showStrategyModal && !showDiscardConfirm, requestCloseStrategyModal);

  const { data: strategy, isLoading, error } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug),
    enabled: !!slug && !!sslug,
    refetchInterval: 5000,
  });

  // Routine instances for ReportBrowser
  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  // Derive controller IDs from active instances for WS executor streaming
  const instances = useMemo(() => strategy?.instances || [], [strategy?.instances]);
  const hasRunning = instances.length > 0;
  const serverName = (strategy?.config?.server_name as string) || "";

  // Executor-mode ids: a running engine tags its own executors with its agent_id.
  // A bot-mode strategy adds nothing here — its controllers tag executors with
  // their own config ids — which is why the live charts below stayed empty for it.
  // The session reviewer widens this with the live bots' controller ids, which it
  // can resolve per session; here we widen it with the newest session's.
  const agentControllerIds = useMemo(
    () => instances.map((inst) => inst.agent_id).filter(Boolean),
    [instances],
  );

  // Depends on `strategy`, not `strategy?.sessions`: the compiler infers the
  // whole object anyway (the optional chain reads it), and claiming the
  // narrower dependency is what made this the one memo it refused to optimize.
  const latestSessionNum = useMemo(
    () => (strategy?.sessions?.length ? Math.max(...strategy.sessions.map((s) => s.number)) : 0),
    [strategy],
  );

  const { data: latestSessionPerf } = useQuery({
    queryKey: ["strategy-session-executors", slug, sslug, latestSessionNum],
    queryFn: () => api.getStrategySessionExecutors(slug, sslug, latestSessionNum),
    enabled: !!slug && !!sslug && latestSessionNum > 0,
    refetchInterval: 10000,
  });

  const controllerIds = useMemo(() => {
    const ids = new Set(agentControllerIds);
    const perf = latestSessionPerf?.performance;
    const live = new Set(perf?.bot_names ?? []);
    for (const c of perf?.controllers ?? []) {
      if (c.controller_id && live.has(c.bot_name)) ids.add(c.controller_id);
    }
    return Array.from(ids);
  }, [agentControllerIds, latestSessionPerf]);

  // Real-time executor data via WS
  const { executors: liveExecutors } = useAgentExecutors(
    hasRunning ? serverName : null,
    controllerIds,
  );

  // Group live executors by connector:pair for charts
  const chartGroups = useMemo(
    () => (serverName ? groupExecutorsByMarket(liveExecutors) : []),
    [liveExecutors, serverName],
  );

  /**
   * Where a run — or one tick of it — is read: the agent's run screen, at a
   * real URL, with the Runs disclosure open. The Lab's own address redirects
   * there, so this could keep pointing at it; it names the destination instead,
   * because a link through a redirect is a link that shows the reader the wrong
   * URL for a frame.
   */
  const labUrl = useCallback(
    (params: Record<string, string | number> = {}) => {
      const query = new URLSearchParams({ open: "runs", strategy: sslug });
      for (const [k, v] of Object.entries(params)) query.set(k, String(v));
      return `/agents/${encodeURIComponent(slug)}?${query}`;
    },
    [slug, sslug],
  );

  /** A beat on the pulse strip is an address: land on that tick's snapshot. */
  const handleOpenTick = useCallback(
    (sessionNum: number, tick: number) => {
      navigate(labUrl({ run: `s${sessionNum}`, tick }));
    },
    [navigate, labUrl],
  );

  if (error && !strategy) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <div className="max-w-sm rounded-lg border border-red-500/30 bg-[var(--color-surface)] p-8 text-center">
          <AlertCircle className="mx-auto mb-3 h-10 w-10 text-[var(--color-red)]" />
          <h2 className="mb-1 text-lg font-semibold">Failed to Load Strategy</h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            {error instanceof Error ? error.message : "An unexpected error occurred."}
          </p>
        </div>
      </div>
    );
  }

  if (isLoading || !strategy) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const liveInstance = instances.find((i) => i.status === "running") ?? instances[0] ?? null;

  const actionClass =
    "flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]";
  /**
   * Every action says its name, at every width.
   *
   * These used to be icons alone in a pane (`dense ? "hidden" : …`) and icons
   * alone on a narrow page, on the theory that a `title` covers it. It does
   * not: a tooltip is hover-only, so on the surface where these are read most
   * — the chat's pane — the header was four unlabelled glyphs, and the only
   * way to find out what one did was to press it. A document icon, a stack, a
   * scroll and a bin are not a vocabulary anyone has agreed to learn.
   *
   * Room comes from the row wrapping (it already does) and from `denseLabel`
   * shortening the one long name, rather than from deleting all four.
   */
  const labelClass = "inline";
  /** "View in fleet" is the only label a 400px column cannot take whole. */
  const denseLabel = (full: string, short: string) => (dense ? short : full);

  return (
    <div className="w-full">
      {/* Header: identity on the left, everything you can do to it on the right */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className={`font-bold text-[var(--color-text)] ${dense ? "text-base" : "text-xl"}`}>
            <span className="text-[var(--color-text-muted)]">{slug}</span>
            <span className="mx-1 text-[var(--color-text-muted)]">/</span>
            {strategy.name}
          </h1>
          {strategy.description && (
            <p className={`mt-1 text-sm text-[var(--color-text-muted)] ${dense ? "line-clamp-2" : ""}`}>
              {strategy.description}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setShowStrategyModal(true)}
            className={actionClass}
            title="Playbook & Learnings"
          >
            <FileText className="h-3.5 w-3.5" />
            <span className={labelClass}>Playbook</span>
          </button>
          {/* The way back into the fleet page (FEAT-096). The link the agent
              band's *Open session* is the other half of: a strategy's work
              is a grouping fact about the fleet, and this is where the reader
              goes to see it beside everything else that is trading. */}
          <button
            onClick={() => navigate(`/bots?scope=agent:${encodeURIComponent(`${slug}.${sslug}`)}`)}
            className={actionClass}
            title="See this strategy's bots and executors beside the rest of the fleet"
          >
            <Layers className="h-3.5 w-3.5" />
            <span className={labelClass}>{denseLabel("View in fleet", "Fleet")}</span>
          </button>
          <button
            onClick={() => setShowRoutinesBrowser(true)}
            className={actionClass}
            title="Routines & Reports"
          >
            <ScrollText className="h-3.5 w-3.5" />
            <span className={labelClass}>Routines</span>
          </button>
          <button
            onClick={() => setShowDeleteConfirm(true)}
            disabled={strategy.status === "running"}
            className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-30"
            title={strategy.status === "running" ? "Stop strategy before deleting" : "Delete strategy"}
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span className={labelClass}>Delete</span>
          </button>
          <AgentControls
            slug={slug}
            sslug={sslug}
            status={strategy.status}
            defaultContext={strategy.default_trading_context || (strategy.config.trading_context as string) || ""}
            agentConfig={strategy.config}
          />
        </div>
      </div>

      {/* What the loop is doing — the first thing on the page, because it is
          the thing a strategy *is*. Above the money: the numbers are the
          outcome, and this is the mechanism that produced them. */}
      <div className="mb-4">
        <LoopPulse
          instance={liveInstance}
          status={strategy.status}
          config={strategy.config}
          onOpenTick={handleOpenTick}
          onSetRestartOnBoot={(enabled) => restartMutation.mutate(enabled)}
          settingRestartOnBoot={restartMutation.isPending}
        />
      </div>

      {/* Meta strip */}
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
          {strategy.sessions.length} session{strategy.sessions.length !== 1 ? "s" : ""}
        </span>
        {/* Said beside the sessions rather than only behind a button: a
            strategy whose whole history is one dry run used to read as one
            that had never run. */}
        {strategy.experiments.length > 0 && (
          <Link
            to={labUrl({ run: `e${Math.max(...strategy.experiments.map((e) => e.number))}` })}
            className="flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-amber-400 transition-colors hover:bg-amber-500/20"
          >
            <FlaskConical className="h-3 w-3" />
            {strategy.experiments.length} dry run{strategy.experiments.length !== 1 ? "s" : ""}
          </Link>
        )}
        <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
          {strategy.slug}
        </span>
        {strategy.agent_id && (
          <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
            {strategy.agent_id}
          </span>
        )}
      </div>

      {/* The money, directly under the pulse: what a strategy is judged on is
          how it did, and reading it used to mean scrolling past the fleet, the
          live charts and the runs to reach the bottom of the page. */}
      <div className={`mb-4 grid grid-cols-1 gap-6 ${dense ? "" : "lg:grid-cols-2"}`}>
        <PerformancePanel slug={slug} sslug={sslug} dense={dense} />
      </div>

      {/* What the loop actually put into the world. Under the money and above
          the charts: the totals say how it went, and this says what is still
          out there doing it. Before this the only answer on this surface was a
          button that navigated to `/bots` — which is to say, the answer cost
          you the strategy you were reading. */}
      <div className="mb-4">
        <DeployedFleet slug={slug} sslug={sslug} serverName={serverName} dense={dense} />
      </div>

      {/* Market Context Strip */}
      {hasRunning && liveExecutors.length > 0 && (
        <div className="mb-6">
          <AgentMarketStrip serverName={serverName} executors={liveExecutors} />
        </div>
      )}

      {/* Live Executor Charts */}
      {hasRunning && chartGroups.length > 0 && (
        <div className="mb-6 space-y-4">
          <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            <Zap className="h-3.5 w-3.5" /> Live Executors
          </h3>
          {chartGroups.map(([key, group]) => (
            <ExecutorChart
              key={key}
              server={serverName}
              executors={group}
              connector={group[0].connector}
              tradingPair={group[0].trading_pair}
              height={dense ? 220 : 300}
            />
          ))}
        </div>
      )}

      {/* Running Instances */}
      {hasRunning && (
        <div className="mb-6 rounded-lg border border-emerald-500/20 bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-emerald-400">
            <Zap className="h-3.5 w-3.5" /> Active Sessions ({instances.length})
          </h3>
          <div className="space-y-3">
            {instances.map((inst) => (
              <InstanceCard key={inst.agent_id} instance={inst} />
            ))}
          </div>
        </div>
      )}

      {/* The way into the runs. The sessions table that used to sit here was
          nine columns, seven of them $0.00, and none of them a tick count. */}
      {showRuns && (
        <div className="mb-6">
          <RunsBand slug={slug} sslug={sslug} labUrl={labUrl} />
        </div>
      )}

      {/* Playbook & Learnings Modal (near full-screen) */}
      {showStrategyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60"
            onClick={requestCloseStrategyModal}
          />
          {/* Modal panel */}
          <div className="relative z-10 flex h-[90vh] w-[95vw] max-w-7xl flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
            {/* Modal header */}
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">
                Playbook & Learnings — {strategy.name}
              </h3>
              <button
                onClick={requestCloseStrategyModal}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            {/* Modal content */}
            <div className="flex-1 overflow-y-auto p-6">
              <div className="grid h-full grid-cols-1 gap-6 lg:grid-cols-2">
                <MarkdownEditor
                  label="Playbook"
                  sublabel="strategy.md"
                  content={strategy.strategy_md}
                  onSave={(value) => api.updateStrategyMd(slug, sslug, value)}
                  invalidateKey={["strategy", slug, sslug]}
                  onDirtyChange={setPlaybookDirty}
                />
                <MarkdownEditor
                  label="Learnings"
                  sublabel="persists across sessions"
                  content={strategy.learnings}
                  onSave={(value) => api.updateStrategyLearnings(slug, sslug, value)}
                  invalidateKey={["strategy", slug, sslug]}
                  onDirtyChange={setLearningsDirty}
                />
              </div>
            </div>
          </div>
          {/* Unsaved-changes confirmation before discarding edits */}
          {showDiscardConfirm && (
            <DiscardChangesDialog
              fileName={
                playbookDirty && learningsDirty
                  ? "strategy.md & learnings"
                  : playbookDirty
                    ? "strategy.md"
                    : "learnings"
              }
              onDiscard={closeStrategyModal}
              onClose={() => setShowDiscardConfirm(false)}
            />
          )}
        </div>
      )}

      {/* Routines ReportBrowser (full-screen overlay; routines live at the agent
          level and are shared across strategies, so filter by the agent slug) */}
      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Strategy"
        isPending={deleteMutation.isPending}
        isError={deleteMutation.isError}
        errorText="Failed to delete strategy. It may be running."
        onConfirm={() => deleteMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete{" "}
        <strong className="text-[var(--color-text)]">{strategy.name}</strong>? This
        cannot be undone.
      </ConfirmDialog>

    </div>
  );
}

/**
 * The five newest runs, and a door to the rest.
 *
 * The workbench is hosted twice — as a page and as the chat's workspace pane —
 * and taking run analysis out of it must not leave either host with no way into
 * a session. This is that way, and it links *out* to the Lab: the one deliberate
 * exception to `AgentPanel`'s "no door out", justified because a three-pane lab
 * does not fit a 640px column.
 *
 * Reads the same `["agent-runs", slug]` query the Lab does, so opening one from
 * here costs no second fetch.
 */
function RunsBand({
  slug,
  sslug,
  labUrl,
}: {
  slug: string;
  sslug: string;
  labUrl: (params?: Record<string, string | number>) => string;
}) {
  const { data: runs = [] } = useQuery({
    queryKey: ["agent-runs", slug],
    queryFn: () => api.getAgentRuns(slug),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  const mine = useMemo(
    () => runs.filter((r) => r.strategy_slug === sslug).slice(0, 5),
    [runs, sslug],
  );
  // One clock for the band, alive only while a run is — the same rule `RunRail`
  // follows over the same list. A bare `Date.now()` here is a render-phase read
  // of a moving value, which is both impure and, on a band that only re-renders
  // when the 5s query settles, a duration that lurches rather than counts.
  const nowSec = useSeconds(mine.some(isLiveRun)) / 1000;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Repeat className="h-3.5 w-3.5" /> Runs
        </h3>
        <Link
          to={labUrl()}
          className="flex items-center gap-0.5 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
        >
          All runs
          <ChevronRight className="h-3 w-3" />
        </Link>
      </div>
      {mine.length === 0 ? (
        <p className="text-xs text-[var(--color-text-muted)]">No runs yet.</p>
      ) : (
        <div className="space-y-1">
          {mine.map((run: AgentRunRow) => (
            <Link
              key={run.run_id}
              to={labUrl({ run: run.run_id })}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${
                  isLiveRun(run)
                    ? "bg-emerald-400"
                    : run.error
                      ? "bg-[var(--color-red)]"
                      : "border border-[var(--color-text-muted)]/50"
                }`}
              />
              <span className="font-mono font-bold text-[var(--color-text)]">
                {runLabel(run)}
              </span>
              <span className="text-[var(--color-text-muted)]">{run.status}</span>
              <span className="ml-auto font-mono text-[10px] text-[var(--color-text-muted)]">
                {runFacts(run, nowSec)}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
