import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ExternalLink, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { DelegationSheet } from "@/components/agent/DelegationSheet";
import { SnapshotDetail } from "@/components/agent/AgentSessionContent";
import { DeploymentLedger } from "@/components/agent/lab/DeploymentLedger";
import { ExperimentDetail, RunOverview } from "@/components/agent/lab/RunOverview";
import { RunRail } from "@/components/agent/lab/RunRail";
import { isLoopRun } from "@/components/agent/lab/runs";
import { AgentFleet } from "@/components/agent/workspace/AgentFleet";
import { MoneyView } from "@/components/agent/workspace/MoneyView";
import { LoopBar } from "@/components/agent/workspace/LoopBar";
import { NowView } from "@/components/agent/workspace/NowView";
import { PlaybookView } from "@/components/agent/workspace/PlaybookView";
import { SectionRail } from "@/components/agent/workspace/SectionRail";
import { SECTION_META } from "@/components/agent/workspace/sectionMeta";
import {
  useSections,
  type SectionId,
} from "@/components/agent/workspace/sections";
import { useWorkspaceAlerts } from "@/components/agent/workspace/useWorkspaceAlerts";
import { pickRun, pickStrategy } from "@/components/agent/workspace/views";
import type { WorkspaceUrlAdapter } from "@/components/agent/workspace/workspaceUrl";
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
 * One agent's page: the run you are looking at, read top to bottom (FEAT-119).
 *
 * This was a spine of twelve entries over a body that swapped — five **Doing**
 * views and the seven **Being** sections — which meant the reader learned a
 * navigation before they learned anything about the agent, and that the money
 * and the deployments could not be read at the same time. The seven Being
 * sections went to the chat's panel (FEAT-118), so the two surfaces answer two
 * questions now: what the agent *is* lives there, what it *did* lives here.
 *
 * What is left is one screen, in two halves:
 *
 * - **The answer stack** is always on it. Who the agent is and the loop's
 *   controls (the header), the strategy and run in scope (the loop bar), then
 *   the run's vitals, whatever wants attention, the last decision whole, the
 *   realized-PnL chart and the table of what it deployed. Nothing to click.
 * - **Five disclosures** under it — Runs, Detail, Money, Fleet, Playbook —
 *   which open *in place* and render nothing at all while closed. That last
 *   part is what makes one screen affordable rather than merely longer: the
 *   fleet browser pulls the whole fleet and the playbook mounts two markdown
 *   editors, and a page that mounted them eagerly would cost more than the
 *   spine it replaces. Which are open is `?open=`, so a screen is a thing you
 *   can send somebody.
 *
 * Down the left is the **index** (FEAT-120): the answer stack and the five
 * bands, with what this screen already knows about each. It is not the spine
 * come back — a rail entry is the *same button* as the band's own header, so
 * the body never swaps and `?open=` still names a set — it is the half the
 * disclosures could not do, which is telling a reader what is on the screen
 * without scrolling a chart's height to find out.
 *
 * Nothing here navigates to another view of itself. A tick is the one thing
 * that covers the screen, as an overlay `?tick=` opens and closing clears — so
 * the reader comes back to the scroll position they left.
 */
export function AgentRunScreen({
  slug,
  adapter,
  header,
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
  header?: (state: { strategy: StrategyDetail | null }) => React.ReactNode;
}) {
  const navigate = useNavigate();

  const { url, set: setParams } = adapter;

  const setOpenParam = useCallback(
    (next: string) => setParams({ open: next || null }),
    [setParams],
  );
  const { open, toggle } = useSections(url.open, setOpenParam);

  // One `["agent", slug]` and one `["agent-runs", slug]` for the whole screen.
  // The header, the loop bar and the bands all want them; react-query dedupes
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

  // One run, in the three readings the answer stack and the Detail bands are
  // cut from. Read at this level so the two are served from one round of
  // requests rather than each band declaring the query it wants.
  const { alerts, decisions, journal, deployments, perf, pnlSeries, sessionNum } =
    useWorkspaceAlerts({ slug, sslug, run: selectedRun, instance });

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
   * The rail's own two moves: back to the answer stack, and open-and-show.
   *
   * The scroller is a ref rather than the window because this screen is a
   * column with its own overflow — `window.scrollTo` would move nothing.
   *
   * Opening scrolls in an effect and not in the click, because at click time
   * the band is still closed and there is nothing to scroll to: the set moves
   * first, React mounts the body, and *then* the pending id is spent. A rail
   * click that *closes* a band scrolls nowhere, which is why the effect checks
   * the set rather than trusting its own request.
   *
   * The request is a ref rather than state, which is the guidance read the
   * right way round: no render depends on it — it is consumed by a side effect
   * one render later — and holding it in state would be a set in an effect to
   * clear it again.
   */
  const bodyRef = useRef<HTMLDivElement>(null);
  const pendingScroll = useRef<SectionId | null>(null);

  useEffect(() => {
    const id = pendingScroll.current;
    if (!id) return;
    pendingScroll.current = null;
    if (!open.includes(id)) return;
    bodyRef.current
      ?.querySelector(`[data-section-body="${id}"]`)
      // Guarded: jsdom has no layout, so it implements no `scrollIntoView`,
      // and a rail click in a test must not throw for want of a viewport.
      ?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [open]);

  // The same action as the band's own header, reached from the index: one rule
  // for what a click does, so `?open=` never depends on which of the two the
  // reader used.
  const selectSection = useCallback(
    (id: SectionId) => {
      toggle(id);
      pendingScroll.current = id;
    },
    [toggle],
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
  const strategyServer =
    (strategy?.config?.server_name as string) || agent.server_name || "";

  /**
   * What the rail says about each section, out of what this screen already has.
   *
   * Money and Fleet carry nothing on purpose. Both headline a *fold* of the
   * whole fleet, which is the query their disclosures exist to defer — and the
   * cheaper numbers lying around (the run rollup, this run's deployments) are a
   * different quantity from the one the band prints, which is the exact
   * confusion FEAT-109 was spent settling.
   */
  const lastTick = decisions[decisions.length - 1]?.tick ?? 0;
  const railFacts = {
    runs: runs.length
      ? `${runs.length}${runs.length >= runLimit ? "+" : ""}`
      : null,
    detail: deployments.length ? `${deployments.length} deployed` : null,
    money: null,
    fleet: null,
    playbook: strategy
      ? `${Object.keys(strategy.config ?? {}).length} settings`
      : null,
  };

  const isOpen = (id: SectionId) => open.includes(id);

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
        onSelectTick={(next) => setParams({ tick: next })}
      />

      <div className="flex min-h-0 flex-1">
        {sslug && (
          <SectionRail
            open={open}
            facts={railFacts}
            nowFact={
              alerts.length > 0
                ? `${alerts.length} alert${alerts.length > 1 ? "s" : ""}`
                : lastTick > 0
                  ? `tick #${lastTick}`
                  : null
            }
            nowAlert={alerts.length > 0}
            onSelect={selectSection}
            onTop={() =>
              bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" })
            }
          />
        )}

        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto">
          {!sslug ? (
            <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
              This agent has no strategies yet, so there is no loop to look at.
            </p>
          ) : (
            <>
              <div className="p-4">
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
                />
              </div>

              <div className="border-t border-[var(--color-border)]">
                <Disclosure
                  id="runs"
                  open={isOpen("runs")}
                  onToggle={toggle}
                  flush
                >
                  {/* A bounded height rather than the page's: the rail scrolls
                      beside its body, which is what it was built to do, and a
                      rail as tall as every run would push the four disclosures
                      below it off the end of the screen. */}
                  <div className="flex h-[70vh] min-h-0">
                    <RunRail
                      runs={runs}
                      strategyFilter={url.strategy}
                      onStrategyFilter={(next) => setParams({ strategy: next })}
                      selectedKey={
                        selectedRun
                          ? `${selectedRun.strategy_slug}:${selectedRun.run_id}`
                          : null
                      }
                      onSelectRun={openRun}
                      isLoading={false}
                      hasMore={runs.length >= runLimit}
                      onShowMore={() => setRunLimit((n) => n + RUN_PAGE)}
                    />
                    <div className="min-h-0 flex-1 overflow-y-auto p-4">
                      <RunBody
                        slug={agent.slug}
                        run={selectedRun}
                        onClearRun={() => setParams({ run: null })}
                      />
                    </div>
                  </div>
                </Disclosure>

                <Disclosure id="detail" open={isOpen("detail")} onToggle={toggle}>
                  {selectedRun && sessionNum > 0 ? (
                    <RunOverview
                      slug={agent.slug}
                      sslug={sslug}
                      sessionNum={sessionNum}
                      serverName={strategyServer}
                      controllerIds={instance ? [instance.agent_id] : undefined}
                      isLiveSession={
                        selectedRun.status === "running" ||
                        selectedRun.status === "paused"
                      }
                      onSelectTick={(next) => setParams({ tick: next })}
                    />
                  ) : (
                    <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
                      Pick a loop run above to read what it ran.
                    </p>
                  )}
                </Disclosure>

                <Disclosure id="money" open={isOpen("money")} onToggle={toggle}>
                  {/* Two numbers, named apart and reconciled (FEAT-109). The
                      headline is the fleet *fold*, which is a different quantity
                      from the run rollup in the vitals above — shown alone,
                      either one is a lie by omission. */}
                  <MoneyView
                    slug={agent.slug}
                    sslug={sslug}
                    strategy={url.strategy}
                    strategies={agent.strategies ?? []}
                    serverName={strategyServer}
                  />
                </Disclosure>

                <Disclosure
                  id="fleet"
                  open={isOpen("fleet")}
                  onToggle={toggle}
                  flush
                >
                  {/* `/bots`' browser is a two-column layout: it gets its own
                      sideways scroller, as it has on that page, rather than
                      letting a narrow window scroll the whole screen sideways. */}
                  <div className="flex h-[70vh] min-h-0 overflow-x-auto">
                    <AgentFleet
                      slug={agent.slug}
                      sslug={sslug}
                      serverName={strategyServer}
                      run={selectedRun}
                    />
                  </div>
                </Disclosure>

                <Disclosure id="playbook" open={isOpen("playbook")} onToggle={toggle}>
                  {/* What the strategy is *told* — its brief, its learnings and
                      its settings — and nothing this screen already answers. The
                      workbench used to be here and it is a page: its title, its
                      controls, its pulse, its ledger and its performance panel
                      each restated something within two inches of themselves,
                      while `strategy.md` was behind a button in a modal. */}
                  {strategy ? (
                    <PlaybookView
                      slug={agent.slug}
                      sslug={sslug}
                      strategy={strategy}
                      onDeleted={() => setParams({ strategy: null })}
                    />
                  ) : (
                    <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
                      Loading this strategy's playbook…
                    </p>
                  )}
                </Disclosure>
              </div>
            </>
          )}
        </div>
      </div>

      {/* One tick, over the screen rather than instead of it — so closing it
          returns the reader to the scroll position and the run they left. */}
      {url.tick !== null &&
        sslug &&
        selectedRun &&
        selectedRun.kind === "session" && (
          <div className="fixed inset-0 z-50 flex flex-col bg-[var(--color-bg)]">
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
        )}
    </div>
  );
}

/**
 * One band of evidence, which renders nothing at all until it is opened.
 *
 * The lazy mount is the whole argument for the disclosure over a section that
 * is simply on the page: `AgentFleet` pulls the entire fleet and
 * `PlaybookView` mounts two markdown editors, so a closed one has to cost
 * what a closed spine entry cost — which is nothing.
 */
function Disclosure({
  id,
  open,
  onToggle,
  flush = false,
  children,
}: {
  id: SectionId;
  open: boolean;
  onToggle: (id: SectionId) => void;
  /** A body that lays itself out edge to edge and brings its own padding. */
  flush?: boolean;
  children: React.ReactNode;
}) {
  const { label, hint, Icon } = SECTION_META[id];
  return (
    <section
      id={`section-${id}`}
      data-section-body={id}
      className="border-b border-[var(--color-border)]"
    >
      <h2>
        <button
          type="button"
          data-section={id}
          aria-expanded={open}
          onClick={() => onToggle(id)}
          className="flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          <ChevronRight
            className={`h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)] transition-transform ${
              open ? "rotate-90" : ""
            }`}
          />
          <Icon className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
          <span className="text-xs font-bold uppercase tracking-widest">
            {label}
          </span>
          <span className="min-w-0 truncate text-[11px] text-[var(--color-text-muted)]">
            {hint}
          </span>
        </button>
      </h2>
      {open && <div className={flush ? "" : "px-4 pb-4"}>{children}</div>}
    </section>
  );
}

/**
 * A conversation, as a run: what it deployed, and a door to what it said.
 *
 * A chat is one of the four kinds of run this rail lists (FEAT-111) and it is
 * the one whose *body* lives somewhere else — the transcript is the chat's, and
 * rebuilding a wide surface inside a disclosure is what FEAT-103's alternative
 * D argued against. But what it **did** is a ledger in the same shape every
 * other run's is (FEAT-110), and that is the half this screen can answer: the
 * row used to say "read it in the chat" and stop, which left the one question
 * an agent's page exists for unanswered for a quarter of its runs.
 *
 * `predates_ledger` is why the empty case is not one sentence. *Deployed
 * nothing* and *ran before Condor wrote down what a chat deployed* look
 * identical on screen and are not the same answer, and telling a reader the
 * first about the second would be a confident lie.
 */
function ConversationRun({ run }: { run: AgentRunRow }) {
  const { data, isLoading } = useQuery({
    queryKey: ["conversation-deployments", run.id],
    queryFn: () => api.getConversationDeployments(run.id),
    enabled: !!run.id,
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="min-w-0 truncate font-medium">
          {run.title || "This chat"}
        </span>
        <Link
          to={`/?conversation=${encodeURIComponent(run.id)}`}
          className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] underline-offset-2 transition-colors hover:text-[var(--color-primary)] hover:underline"
        >
          Read it in the chat <ExternalLink className="h-3 w-3" />
        </Link>
      </div>

      {isLoading ? (
        <div className="flex h-24 items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
        </div>
      ) : data?.predates_ledger ? (
        <p className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-xs text-[var(--color-text-muted)]">
          This chat ran before Condor recorded what a conversation deployed, so
          there is nothing to show — which is not the same as it having deployed
          nothing.
        </p>
      ) : (
        <DeploymentLedger rows={data?.deployments ?? []} />
      )}
    </div>
  );
}

/**
 * What a rail row opens, for the three kinds that are not this screen.
 *
 * A loop run *is* the screen — selecting one re-scopes everything above — so it
 * says so rather than drawing a second copy of the answer stack inside the
 * disclosure that selected it.
 */
function RunBody({
  slug,
  run,
  onClearRun,
}: {
  slug: string;
  run: AgentRunRow | null;
  /** Put the selection back to the newest run — the sheet's way out. */
  onClearRun: () => void;
}) {
  if (!run) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        This agent has no runs yet.
      </p>
    );
  }
  if (run.kind === "delegation") {
    // The one place a background task is read already exists — the dock, the
    // fleet card and an agent's history all open this sheet — and a rail row is
    // the fourth caller, not a fourth copy.
    return <DelegationSheet task={delegationTask(run, slug)} onClose={onClearRun} />;
  }
  if (run.kind === "conversation") return <ConversationRun run={run} />;
  if (run.kind === "experiment") {
    return (
      <ExperimentDetail slug={slug} sslug={run.strategy_slug} number={run.number} />
    );
  }
  return (
    <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
      Session {run.number} is the run on this screen — its vitals, its last
      decision and what it deployed are above.
    </p>
  );
}
