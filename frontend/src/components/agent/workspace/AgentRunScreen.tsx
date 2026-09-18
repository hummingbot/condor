import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Activity, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { SnapshotDetail } from "@/components/agent/session/Snapshot";
import { isLoopRun, liveControllerIds } from "@/components/agent/lab/runs";
import { AgentFleet } from "@/components/agent/workspace/AgentFleet";
import { declaredServerOf } from "@/components/agent/workspace/fleet";
import { LoopBar } from "@/components/agent/workspace/LoopBar";
import { NowView } from "@/components/agent/workspace/NowView";
import { PlaybookView } from "@/components/agent/workspace/PlaybookView";
import { RunsBand } from "@/components/agent/workspace/RunsBand";
import { SECTION_META } from "@/components/agent/workspace/sectionMeta";
import {
  PANE_SECTIONS,
  openForPaneSection,
  pageSection,
  type PaneSection,
} from "@/components/agent/workspace/sections";
import { useRunReading } from "@/components/agent/workspace/useRunReading";
import {
  ownsStrategy,
  pickRun,
  pickStrategy,
} from "@/components/agent/workspace/views";
import type {
  WorkspaceUrlAdapter,
  WorkspaceUrlPatch,
} from "@/components/agent/workspace/workspaceUrl";
import {
  api,
  type AgentRunRow,
  type StrategyDetail,
} from "@/lib/api";
import { agentQuery } from "@/lib/queryClient";

/**
 * How many runs one page of the rail holds (FEAT-111).
 *
 * The rail lists four kinds now, and conversations are the unbounded one: a
 * year of chatting is hundreds of rows, and a five-second poll that pulled all
 * of them would undo the cheapness that licenses the poll in the first place.
 */
const RUN_PAGE = 100;

/**
 * One agent's run screen: the run you are looking at, one section at a time.
 *
 * Two hosts, one layout. Beside a conversation (the chat's side panel) and on
 * the whole window (`/agents/:slug`), it is the same thing: who the agent is
 * and the loop's controls (the header), the strategy and run in scope (the
 * loop bar), then a tab strip — Now, Runs, Fleet, Playbook — over one
 * body. The page used to be an index down the left over five disclosures
 * stacked under the answer stack; expanding the panel landed on a different
 * screen rather than a bigger one, so full screen is now the panel made big.
 *
 * Only the tab's *address* differs: the pane keeps its own key (`paneUrl`),
 * the page spells it `?open=`. A tab renders nothing until it is chosen, which
 * is what keeps the fleet fold and the playbook's editors off a first paint.
 *
 * A tick is the one thing that covers the screen, as an overlay `?tick=` opens
 * and closing clears — so the reader comes back to the tab they left.
 */
export function AgentRunScreen({
  slug,
  adapter,
  header,
  variant = "page",
  section: paneSection = "now",
  onSection,
}: {
  slug: string;
  /** Where this screen's four parameters are read and written. */
  adapter: WorkspaceUrlAdapter;
  /**
   * Above the loop bar — `<WorkspaceHeader/>`, from the page.
   *
   * A function rather than a node, as `WorkspaceSheet`'s own `header` is, and
   * for the same reason: the header carries the loop's start/pause/stop
   * controls, which act on the strategy *this* component resolved from the URL.
   * Handing it in as a node would mean the page resolving the scope a second
   * time, and the two answers drifting the first time the rule changed.
   */
  header?: (state: {
    strategy: StrategyDetail | null;
    run: AgentRunRow | null;
    section: PaneSection;
  }) => React.ReactNode;
  /**
   * `"page"` is the whole window, `"pane"` the same screen beside a
   * conversation. The layout is one; the pane's tick overlay covers the pane
   * only, and its tab lives in the pane's address rather than `?open=`.
   */
  variant?: "page" | "pane";
  /** The pane's open tab. Ignored on the page, where `?open=` names it. */
  section?: PaneSection;
  /**
   * Move the pane's tab — with a patch to apply in the same write, for a door
   * that both selects a run and shows the Runs tab. Ignored on the page.
   */
  onSection?: (next: PaneSection, patch?: WorkspaceUrlPatch) => void;
}) {
  const isPane = variant === "pane";
  const navigate = useNavigate();

  const { url, set: setParams } = adapter;

  // One tab at a time on both hosts; only where it is written differs.
  const section: PaneSection = isPane ? paneSection : pageSection(url.open);
  const setSection = useCallback(
    (next: PaneSection, patch?: WorkspaceUrlPatch) => {
      if (isPane) {
        onSection?.(next, patch);
        return;
      }
      setParams({ ...patch, open: openForPaneSection(next) || null });
    },
    [isPane, onSection, setParams],
  );

  // One `["agent", slug]` and one `["agent-runs", slug]` for the whole screen:
  // the header, the loop bar and the bands all want them, and react-query
  // dedupes the keys. Nothing live here comes off the agent key — the countdown
  // and cadence are `["strategy", slug, sslug]` (polled only while an engine is
  // up, read once for an idle strategy), the rail `["agent-runs", ...]` — so it
  // takes the shared gate (`agentQuery`, PERF-305/PERF-343).
  const { data: agent, isLoading } = useQuery(agentQuery(slug));

  // The rail's window, not a filter (FEAT-111). An install that has been
  // chatted with for a year has hundreds of conversations, and pulling the
  // archive on a five-second poll is how a cheap rail stops being cheap. The
  // window widens on request and stays widened for the visit.
  //
  // Widening re-keys the query, and a key with no cache entry reads `[]` until
  // it lands: no selected run, so every band would say there is nothing to show
  // for as long as the wider page takes — triggered by the control whose job is
  // to show more (CORR-378). So the previous window stays on screen meanwhile.
  // While it does, `hasMoreRuns` is false (100 rows against a limit of 200), so
  // the rail's "Show older" and the `+` drop until the wider page lands.
  const [runLimit, setRunLimit] = useState(RUN_PAGE);
  const { data: runs = [] } = useQuery({
    queryKey: ["agent-runs", slug, runLimit],
    queryFn: () => api.getAgentRuns(slug, runLimit),
    enabled: !!slug,
    refetchInterval: 5000,
    placeholderData: keepPreviousData,
  });

  // Hoisted rather than reached through in the dependency lists: the compiler
  // infers the whole `agent` as the dependency and refuses to preserve a memo
  // whose declared one is narrower.
  const strategies = agent?.strategies;
  const sslug = useMemo(
    () => pickStrategy(strategies ?? [], runs, url.strategy),
    [strategies, runs, url.strategy],
  );
  // What the bands that *narrow* read: `?strategy=` only when the agent owns
  // it. Not `sslug` — a bare URL means every strategy to the rail and the fold
  // — and not the raw param, which a stale link can point at nothing (CORR-397).
  const narrow = useMemo(
    () => ownsStrategy(strategies ?? [], url.strategy),
    [strategies, url.strategy],
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

  // The strategy detail ships `strategy.md` and `learnings.md` and walks the
  // session index and performance cache on the server, so it polls only while
  // it has something live to report: an instance exists exactly while an
  // engine (a session or a dry_run/run_once experiment) is running or paused,
  // the only time `last_tick_at`/`tick_count` move. An idle strategy is read
  // once (PERF-374); start/stop/pause/resume invalidate the key
  // (`invalidateLifecycle`), and the predicate is re-evaluated on that refetch,
  // so the poll re-arms without a reload. A loop started elsewhere (Telegram,
  // MCP, restart_on_boot) shows on the next focus/reconnect/remount.
  const { data: strategy = null } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug!),
    enabled: !!slug && !!sslug,
    refetchInterval: (q) =>
      (q.state.data?.instances?.length ?? 0) > 0 ? 5000 : false,
  });

  // The live engine behind the selected run, for the cadence and the countdown
  // — the two facts a run row deliberately does not carry.
  const instances = strategy?.instances;
  const runAgentId = selectedRun?.agent_id;
  const instance = useMemo(
    () => instances?.find((i) => i.agent_id === runAgentId) ?? null,
    [instances, runAgentId],
  );

  // One run, in the three readings the answer stack is cut from. Read at this
  // level so the bands are served from one round of requests rather than each
  // declaring the query it wants.
  const { alerts, decisions, journal, deployments, perf, pnlSeries, sessionNum } =
    useRunReading({ slug, sslug, run: selectedRun, instance });

  // The controllers whose executors the Runs tab's market chart streams: the
  // engine's own id, widened with every live bot controller of the run.
  const instanceId = instance?.agent_id;
  const streamIds = useMemo(
    () => liveControllerIds(perf, instanceId ? [instanceId] : []),
    [perf, instanceId],
  );

  /**
   * Opening a run, which is now four different things (FEAT-111).
   *
   * A loop run and a delegation are *selections*: the rail stays, the screen
   * above it re-scopes, and `?run=` says which. A conversation is not — the
   * chat is the surface for a conversation, and rebuilding a wide surface
   * inside a narrow one is what FEAT-103's alternative D argued against. So a
   * chat row navigates to the chat, carrying the conversation it wants opened.
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

  /**
   * A run named from inside a tab — the Playbook's session and dry-run counts:
   * the selection a rail row makes, with Runs brought forward to show it.
   */
  const showRun = useCallback(
    (run: string) => setSection("runs", { strategy: sslug, run }),
    [setSection, sslug],
  );

  // The page has already guarded this by the time it mounts the screen — the
  // query is shared and warm — so this only shows on a hard reload racing it.
  if (isLoading || !agent) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  // Where this agent's work actually happens: the strategy's own configured
  // server, else the agent's pin. `/bots` reads the ambient server and the
  // fleet map deliberately does not, so a rooted fleet has to read the agent's
  // (FEAT-108) — otherwise an agent trading on another server has a Fleet
  // disclosure that cannot fetch its own bots.
  //
  // `declaredServerOf` over the strategy *summary*, the rule the home applies
  // to the row that links here (ARCH-382): the summary is in the agent query
  // already warm on arrival, where the `["strategy", …]` detail is a second
  // fetch — reading its config would fold the pin's fleet on first paint and
  // flip to the strategy's once the detail landed.
  const strategyServer = declaredServerOf(
    agent,
    (agent.strategies ?? []).find((s) => s.slug === sslug) ?? null,
  );

  // The window bounds chats and delegations only; loop runs always ride along
  // (CORR-376). So "there may be more" is a count of the kinds that page, and a
  // strategy the playbook says has sessions but whose runs are not in the
  // window is *outside it* — not a strategy that "has not run yet". The second
  // guard covers a hand-edited `limit` and servers older than that fix.
  const hasMoreRuns =
    runs.filter((r) => !isLoopRun(r.kind)).length >= runLimit;
  const showOlderRuns = () => setRunLimit((n) => n + RUN_PAGE);
  const runsOutsideWindow =
    (strategy?.sessions?.length ?? 0) > 0 && scopedRuns.length === 0;

  // Counts only: a tab has the width of its name and a number, no more. Fleet
  // carries nothing on purpose — it headlines a fold of the whole fleet, the
  // query the tab exists to defer.
  const facts: Record<PaneSection, string | null> = {
    now: alerts.length > 0 ? String(alerts.length) : null,
    runs: runs.length ? `${runs.length}${hasMoreRuns ? "+" : ""}` : null,
    fleet: null,
    playbook: null,
  };

  const nowBody = sslug ? (
    <NowView
      slug={agent.slug}
      sslug={sslug}
      sessionNum={sessionNum}
      alerts={alerts}
      decisions={decisions}
      deployments={deployments}
      perf={perf}
      journal={journal}
      pnlSeries={pnlSeries}
      onOpenTick={(next) => setParams({ tick: next })}
      onShowOlderRuns={runsOutsideWindow ? showOlderRuns : undefined}
      variant={variant}
    />
  ) : null;

  const bandBody = (id: Exclude<PaneSection, "now">): React.ReactNode => {
    if (!sslug) return null;
    switch (id) {
      case "runs":
        return (
          <RunsBand
            slug={agent.slug}
            runs={runs}
            selectedRun={selectedRun}
            strategyFilter={narrow}
            onStrategyFilter={(next) => setParams({ strategy: next })}
            onSelectRun={openRun}
            onClearRun={() => setParams({ run: null })}
            hasMore={hasMoreRuns}
            onShowMore={showOlderRuns}
            onShowOlderRuns={runsOutsideWindow ? showOlderRuns : undefined}
            onOpenTick={(run, tick) =>
              setParams({ strategy: run.strategy_slug, run: run.run_id, tick })
            }
            onShowNow={() => setSection("now")}
            serverName={strategyServer}
            controllerIds={streamIds}
          />
        );
      case "fleet":
        /* `/bots`' browser is a two-column layout: it gets its own sideways
           scroller, as it has on that page, rather than letting a narrow
           window scroll the whole screen sideways. */
        return (
          <div className="flex h-full min-h-0 overflow-x-auto">
            <AgentFleet
              slug={agent.slug}
              sslug={sslug}
              serverName={strategyServer}
              run={selectedRun}
            />
          </div>
        );
      case "playbook":
        /* What the strategy is *told* — its brief, its learnings and its
           settings — and nothing this screen already answers. */
        return strategy ? (
          <PlaybookView
            slug={agent.slug}
            sslug={sslug}
            strategy={strategy}
            onDeleted={() => setParams({ strategy: null })}
            onOpenRun={showRun}
          />
        ) : (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
            Loading this strategy's playbook…
          </p>
        );
    }
  };

  const tickOverlay = url.tick !== null &&
    sslug &&
    selectedRun &&
    selectedRun.kind === "session" && (
      // Over the screen rather than instead of it — so closing it returns the
      // reader to the tab and the run they left. In the pane it covers the
      // pane only: the conversation beside it stays readable.
      <div
        className={`${
          isPane ? "absolute" : "fixed"
        } inset-0 z-50 flex flex-col bg-[var(--color-bg)]`}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
          <span className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Tick #{url.tick} · session {selectedRun.number}
          </span>
          <button
            type="button"
            onClick={() => setParams({ tick: null })}
            aria-label="Close tick"
            className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <SnapshotDetail
            slug={agent.slug}
            sslug={sslug}
            sessionNum={selectedRun.number}
            tick={url.tick}
          />
        </div>
      </div>
    );

  return (
    <div className="relative flex h-full min-h-0 w-full flex-col">
      {header?.({ strategy, run: selectedRun, section })}

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
        onSelectTick={(next) => setParams({ tick: next })}
      />

      {sslug && (
        <PaneTabs
          active={section}
          facts={facts}
          nowAlert={alerts.length > 0}
          wide={!isPane}
          onSelect={(next) => setSection(next)}
        />
      )}

      <div
        key={section}
        data-pane-section={section}
        className={`min-h-0 flex-1 ${
          // The run rail and the fleet browser bring their own scrollers;
          // everything else scrolls here.
          section === "fleet" || section === "runs"
            ? "flex flex-col overflow-hidden"
            : "overflow-y-auto"
        }`}
      >
        {!sslug ? (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
            This agent has no strategies yet, so there is no loop to look at.
          </p>
        ) : section === "now" ? (
          <div className={isPane ? "p-4" : "mx-auto w-full max-w-6xl p-6"}>
            {nowBody}
          </div>
        ) : section === "runs" || section === "fleet" ? (
          bandBody(section)
        ) : (
          <div className={isPane ? "p-4" : "mx-auto w-full max-w-6xl p-6"}>
            {bandBody(section)}
          </div>
        )}
      </div>

      {tickOverlay}
    </div>
  );
}

/**
 * The screen's sections, across the top of it — and the body swaps on a click,
 * because one section at a time is what lets each of them take the room.
 */
function PaneTabs({
  active,
  facts,
  nowAlert,
  wide = false,
  onSelect,
}: {
  active: PaneSection;
  facts: Partial<Record<PaneSection, string | null>>;
  nowAlert: boolean;
  /** The page's strip: the same tabs with the room a window has. */
  wide?: boolean;
  onSelect: (id: PaneSection) => void;
}) {
  return (
    <nav
      aria-label="Sections"
      role="tablist"
      data-pane-tabs
      className={`flex shrink-0 items-stretch overflow-x-auto border-b border-[var(--color-border)] [scrollbar-width:none] ${
        wide ? "gap-2 px-4" : "px-2"
      }`}
    >
      {PANE_SECTIONS.map((id) => {
        const { label, Icon } =
          id === "now" ? { label: "Now", Icon: Activity } : SECTION_META[id];
        const fact = facts[id];
        const on = id === active;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={on}
            data-pane-tab={id}
            onClick={() => onSelect(id)}
            title={id === "now" ? "The run: vitals, last decision, what it deployed" : SECTION_META[id].hint}
            className={`-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-2 font-medium transition-colors ${
              wide ? "py-2.5 text-sm" : "py-2 text-xs"
            } ${
              on
                ? "border-[var(--color-primary)] text-[var(--color-text)]"
                : "border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" />
            {label}
            {fact && (
              <span
                className={`rounded-full px-1.5 py-px font-mono text-[10px] ${
                  id === "now" && nowAlert
                    ? "bg-amber-500/15 text-amber-500"
                    : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                }`}
              >
                {fact}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
