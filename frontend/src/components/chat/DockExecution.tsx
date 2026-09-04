import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  decisionHref,
  declaredServerOf,
  fleetRows,
  rowHref,
  dueInSec,
  type FleetRow,
} from "@/components/agent/workspace/fleet";
import { ControllerToggle } from "@/components/perf/ControllerToggle";
import {
  executionCounts,
  executionRows,
  openRows,
  visibleRows,
  type ExecutionRow,
} from "@/components/chat/executionTree";
import { useFleetData } from "@/hooks/useFleetData";
import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import { api } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import {
  formatCompactVolume,
  formatCurrencyPnl,
  formatRuntimeHours,
  pnlColor,
  shortBotName,
} from "@/lib/formatters";
import { quoteConverter, runningLeaves } from "@/lib/perf-population";
import { UNATTACHED_BOT, controllerNodeId, type PerfLeaf } from "@/lib/perf-tree";

/**
 * The columns, left to right — the header row and every body row read from it.
 *
 * One list rather than two parallel ones, because a table whose `<th>`s and
 * `<td>`s are written out separately drifts the moment a column is inserted in
 * the middle, and the failure is silent: every number is still rendered, one
 * heading to the left of what it means.
 *
 * `num` is what is right-aligned and tabular; the label column is not. `hint`
 * is the `title`, because a five-character heading has to be able to say what
 * it is somewhere.
 */
const COLUMNS: {
  key: string;
  label: string;
  hint: string;
  num: boolean;
  /** Fixed px, or `undefined` for the one column that absorbs the slack. */
  width?: number;
}[] = [
  { key: "controller", label: "Controller", hint: "Config id — click for its scope in /bots", num: false },
  { key: "pair", label: "Pair", hint: "The market it trades", num: false, width: 76 },
  { key: "exec", label: "Exec", hint: "Live executors it is running now", num: true, width: 38 },
  { key: "up", label: "Up", hint: "Time since it was deployed", num: true, width: 46 },
  { key: "vol", label: "Vol", hint: "Volume traded", num: true, width: 58 },
  { key: "realized", label: "Real.", hint: "Realized PnL", num: true, width: 68 },
  { key: "unrealized", label: "Unreal.", hint: "Unrealized PnL on open positions", num: true, width: 68 },
  { key: "net", label: "Net", hint: "The controller's own total — not realized + unrealized", num: true, width: 70 },
  // No heading: the button in it says what it does, and two glyphs of column
  // title would cost more width than the button itself.
  { key: "act", label: "", hint: "Pause or start the controller", num: false, width: 26 },
];

/** The width every column but the first is worth, plus a floor for the first. */
const FIXED_PX = COLUMNS.reduce((sum, c) => sum + (c.width ?? 0), 0);
/** Below this the controller column stops being readable and the table scrolls. */
const MIN_LABEL_PX = 120;
export const TABLE_MIN_PX = FIXED_PX + MIN_LABEL_PX;

/**
 * How many characters of a bot name a group header keeps.
 *
 * A deploy name is a config id with stamps bolted onto it, and a rolled-back
 * fleet reaches ninety-odd characters — at 10px that is a header line running
 * the full width of the panel, which reads as a heading *of* the table rather
 * than a row inside it and collides with the column titles above it. A cap
 * that is short of the narrowest useful panel width means the header can never
 * be the widest thing on screen, whatever the fleet is called.
 */
const BOT_NAME_MAX = 34;

/**
 * The name with its middle taken out, never its end.
 *
 * Both ends carry identity: the head is the config the fleet runs, the tail is
 * the deploy stamp that tells two runs of it apart. A plain right-hand ellipsis
 * — which is what CSS truncation gives — throws the tail away and renders two
 * simultaneous deploys of one config as the same header twice, so the cut is
 * taken out of the middle, where the bytes are a repeated date. The button's
 * `title` still carries the whole name.
 */
function clipBotName(name: string): string {
  const short = shortBotName(name);
  if (short.length <= BOT_NAME_MAX) return short;
  const head = Math.ceil((BOT_NAME_MAX - 1) / 2);
  const tail = BOT_NAME_MAX - 1 - head;
  return `${short.slice(0, head)}…${short.slice(short.length - tail)}`;
}

/**
 * What is trading right now on the server this conversation is about — and who
 * is driving it (FEAT-114).
 *
 * The deployed fleet, in the shape the perf browser folds it, read **owner
 * first**: one row per agent, each expanding into the bots, controllers and
 * executors that agent deployed, and then the controllers nobody owns under
 * *Outside Condor* / *Before the ledger*. Every row deep-links into `/bots` by
 * the `?scope=` id the browser reads (FEAT-084, FEAT-086) — and builds that id
 * by calling `controllerNodeId` on the same `PerfLeaf` the browser folds, so
 * the two can only drift if the browser's own tree does.
 *
 * **This is where `/fleet` went.** That page answered *"what is every agent
 * doing"* on a screen of its own, and a second surface over one fleet is a
 * second fold of one set of records — the disagreement ARCH-324 exists to
 * prevent. So the page is gone, its rules (`workspace/fleet.ts`) are read from
 * here, and the agent is a *level of this tree* rather than a tab of its own:
 * the panel's selector is still the server, exactly as the rail's docstring
 * requires, and a level was added to it.
 *
 * **The trade-off is one server.** The panel reads `dockServer` — the chat
 * slot's, falling back to the ambient one — and the sheet's bar names it. An
 * agent trading somewhere else is named in a line at the foot rather than
 * shown as a zero.
 *
 * **Running is the kill switch, not `status`.** The `/bots` payload hardcodes
 * `"running"` for every controller it reports; what actually stops one is
 * `config.manual_kill_switch`. `leafFromController` is where that is resolved,
 * so nothing in this file reads `c.status` — on the one panel whose whole job
 * is answering "what is running", a paused controller listed as live would be
 * the exact wrong answer.
 *
 * **Paused controllers are listed too, and can be started from here.** A paused
 * controller is still deployed: it holds its config, its history and whatever
 * position it was left with, and it is one click from quoting again. Hiding it
 * made the panel answer "what exists on this bot" with "what is quoting", so a
 * fleet somebody had paused read as a fleet that had gone missing — and the
 * repair was three navigations away, in the browser. The row keeps the pause
 * and start on it: one click, no confirmation, because pausing cancels orders
 * but takes nothing down and the same click puts it back. Stopping the *bot* is
 * still the browser's, where an armed confirmation has room to say what dies.
 *
 * Mounted only while the section is open (see `DockSection`): closed, none of
 * this is fetched. Open, it costs nothing a reader with `/bots` up was not
 * already paying — `useFleetData` holds the same query keys, and `history:
 * false` keeps the performance-history walk out of a panel that draws no curve.
 */
export function DockExecution({
  server,
  onOpenAgent,
}: {
  server: string;
  /**
   * Open an agent's panel in the pane — the agent this row is about, not the
   * conversation's. Absent, the row navigates to the agent's own page instead,
   * which is what a host with no pane can offer.
   */
  onOpenAgent?: (slug: string) => void;
}) {
  const navigate = useNavigate();

  // One call, under the keys `/bots` already holds — so the socket, the poll
  // and the caches are shared rather than doubled, and a reader with the
  // browser open pays nothing for this panel. No history walk: this folds, it
  // does not chart, and the walk costs a paged request per controller.
  const fleet = useFleetData(server, { population: "running", history: false });

  // Same key and interval `AgentChatTab` and the rail already poll, so the
  // agent rows' liveness arrives with a request nobody made for them.
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
    refetchInterval: 10000,
  });

  // A clock only while something is looping: the countdown is the one thing in
  // this panel that moves on its own, and an interval running under a fleet
  // that is idle is an interval running for nothing.
  const anyRunning = agents.some((agent) => agent.status === "running");
  const now = useSeconds(anyRunning);
  const nowSec = now / 1000;

  /**
   * Everything trading, in the browser's one vocabulary and its one attribution.
   *
   * `runningLeaves` rather than a second construction here: it is the same
   * function `/bots` and the Money view build their populations with, so this
   * panel cannot disagree with them about who owns what.
   *
   * Sorted by bot then label and *not* by state, which is the order this panel
   * has always drawn: a controller keeps its place in its bot's list when it is
   * paused, so pausing one from here does not make the row jump out from under
   * the cursor that just paused it.
   */
  const leaves = useMemo(() => {
    const all = runningLeaves({
      controllers: fleet.controllers,
      executors: fleet.executors,
      owners: fleet.owners,
      deeds: fleet.deeds,
    });
    const controllers = all
      .filter((leaf) => leaf.kind === "controller")
      .sort((a, b) => a.bot.localeCompare(b.bot) || a.label.localeCompare(b.label));
    return [...controllers, ...all.filter((leaf) => leaf.kind !== "controller")];
  }, [fleet.controllers, fleet.executors, fleet.owners, fleet.deeds]);

  const convert = useMemo(() => quoteConverter(fleet.convert), [fleet.convert]);
  const currencySymbol = fleet.currencySymbol ?? "$";

  const rows = useMemo(
    () => executionRows({ leaves, deeds: fleet.deeds, agents, convert, now }),
    [leaves, fleet.deeds, agents, convert, now],
  );

  // Only what the reader has actually clicked; the default for everything else
  // is a question about the fleet's shape, and `openRows` answers it.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const open = useMemo(() => openRows(rows, toggled), [rows, toggled]);
  const shown = useMemo(() => visibleRows(rows, open), [rows, open]);
  const { controllers: deployedCount, paused } = useMemo(
    () => executionCounts(rows),
    [rows],
  );

  /**
   * How many live executors each controller is running, and how many nobody
   * claims.
   *
   * Keyed on bot + config id, because one config deployed to two bots is two
   * independent controllers sharing an id (CORR-241). A leaf with no
   * `controllerId` is one `leafFromExecutor` could not attach to any live
   * controller — the hand-opened positions and the ones left behind — and they
   * are counted rather than dropped.
   */
  const { byController, unattached, liveExecutors } = useMemo(() => {
    const counts = new Map<string, number>();
    let orphans = 0;
    let total = 0;
    for (const leaf of leaves) {
      if (leaf.kind !== "executor") continue;
      total += 1;
      if (!leaf.controllerId) {
        orphans += 1;
        continue;
      }
      const key = controllerKey({ bot_name: leaf.bot, controller_id: leaf.controllerId });
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return { byController: counts, unattached: orphans, liveExecutors: total };
  }, [leaves]);

  /** The agents' own liveness, from the rules `/fleet` used to be markup over. */
  const live = useMemo(() => {
    const by = new Map<string, FleetRow>();
    for (const row of fleetRows(agents, nowSec)) by.set(row.slug, row);
    return by;
  }, [agents, nowSec]);
  const looping = useMemo(
    () => [...live.values()].filter((row) => row.live?.status === "running").length,
    [live],
  );

  /**
   * The agents that trade somewhere else — named, not shown as a zero.
   *
   * The desk is one server, deliberately, and an agent whose declared server is
   * not this one has no records here to fold. A row of dashes would read as *it
   * made nothing*; a line saying where it does trade is the honest version of
   * the same fact, and it links out to the page that can answer.
   */
  const elsewhere = useMemo(() => {
    const here = new Set(
      rows.flatMap((row) => (row.agent ? [row.agent.slug] : [])),
    );
    return agents
      .filter((agent) => !here.has(agent.slug) && (agent.strategies ?? []).length > 0)
      .map((agent) => {
        const row = live.get(agent.slug);
        const declared = declaredServerOf(agent, row?.strategy ?? null);
        return { row, declared };
      })
      .filter(
        (entry): entry is { row: FleetRow; declared: string } =>
          !!entry.row && !!entry.declared && entry.declared !== server,
      );
  }, [agents, rows, live, server]);

  const footer = (
    <button
      type="button"
      onClick={() => navigate("/bots")}
      className="flex w-full items-center gap-1 px-3 py-1.5 text-left text-[11px] text-[var(--color-primary)] transition-colors hover:bg-[var(--color-surface-hover)]"
    >
      Open execution
      <ArrowRight className="h-3 w-3" />
    </button>
  );

  if (fleet.error) {
    return (
      <div className="flex flex-col">
        <p className="px-3 py-2 text-[11px] text-[var(--color-red)]">
          Could not read {server}&apos;s fleet.
        </p>
        {footer}
      </div>
    );
  }

  if (fleet.isLoading && fleet.controllers.length === 0) {
    return (
      <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
        Reading {server}…
      </p>
    );
  }

  if (!deployedCount && !unattached) {
    return (
      <div className="flex flex-col">
        <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
          No controllers deployed on {server}.
        </p>
        {footer}
      </div>
    );
  }

  const toggle = (id: string) =>
    setToggled((prev) => ({ ...prev, [id]: !open.has(id) }));

  return (
    <div className="flex flex-col">
      {/* The three figures the fleet page uniquely answered, kept on screen
          while the list scrolls — the counts are the answer, the rows are the
          detail. */}
      <div
        className="sticky top-0 z-10 flex items-baseline gap-2 bg-[var(--color-bg)] px-3 pb-1 pt-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]"
        data-testid="execution-counts"
      >
        <span className="min-w-0 flex-1 truncate">
          {looping} looping · {deployedCount} controller
          {deployedCount === 1 ? "" : "s"}
          {/* Said here rather than left to be counted down the rows: how much
              of a fleet is actually quoting is the first thing this panel is
              read for, and a paused row looks like a quiet one from a distance. */}
          {paused > 0 && (
            <span className="text-[var(--color-yellow)]"> · {paused} paused</span>
          )}
        </span>
        <span className="shrink-0 tabular-nums">
          {liveExecutors} executor{liveExecutors === 1 ? "" : "s"}
        </span>
      </div>

      {/* A table, not a list of cards. Every controller answers the same eight
          questions, and eight answers per row only stay comparable when they
          are in eight columns. The agent and bot rows above them span the whole
          table instead: they are headings with a fold attached, not eight more
          numbers, and giving them the controller's columns would have put an
          agent's total under a heading that says "Controller". It fits whatever
          width the panel was dragged to and only scrolls sideways below
          `TABLE_MIN_PX`, where the controller name would otherwise be squeezed
          out of legibility. */}
      <div className="min-w-0 overflow-x-auto">
        <table
          className="w-full table-fixed border-collapse text-[11px]"
          style={{ minWidth: TABLE_MIN_PX }}
        >
          {/* Fixed layout, so the table is exactly as wide as the column it is
              in and the controller name absorbs whatever is left. Letting the
              browser size from content instead pushed Net off the right edge on
              a fleet whose bot names run to sixty characters — the widest cell
              won the negotiation and the most important number lost it. */}
          <colgroup>
            {COLUMNS.map((col) => (
              <col key={col.key} style={col.width ? { width: col.width } : undefined} />
            ))}
          </colgroup>
          <thead>
            <tr className="text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  scope="col"
                  title={col.hint}
                  className={`px-1.5 pb-1 font-medium ${
                    col.num ? "text-right" : "text-left"
                  } ${col.key === "controller" ? "pl-3" : ""} ${
                    col.key === "act" ? "pr-3" : ""
                  }`}
                >
                  {col.label || <span className="sr-only">{col.hint}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <Fragment key={row.id}>
                {row.kind === "controller" ? (
                  <ControllerRow
                    row={row}
                    server={server}
                    executors={byController.get(row.leaves[0].id) ?? 0}
                    now={now}
                    convert={convert}
                    symbol={currencySymbol}
                    onOpen={(scope) => navigate(`/bots?scope=${scope}`)}
                  />
                ) : (
                  <tr>
                    <td colSpan={COLUMNS.length} className="p-0">
                      {row.kind === "agent" ? (
                        <AgentRow
                          row={row}
                          live={row.agent ? live.get(row.agent.slug) ?? null : null}
                          nowSec={nowSec}
                          symbol={currencySymbol}
                          open={open.has(row.id)}
                          onToggle={() => toggle(row.id)}
                          onOpenAgent={onOpenAgent}
                        />
                      ) : (
                        <BotRow
                          row={row}
                          open={open.has(row.id)}
                          onToggle={() => toggle(row.id)}
                          onOpen={() => navigate(`/bots?scope=bot:${row.label}`)}
                        />
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {unattached > 0 && (
        <button
          type="button"
          onClick={() => navigate("/bots?population=running")}
          title="Live executors no running controller claims"
          className="flex w-full items-baseline gap-2 border-t border-[var(--color-border)] px-3 py-1 text-left text-[11px] transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          <span className="min-w-0 flex-1 truncate text-[var(--color-text-muted)]">
            {UNATTACHED_BOT}
          </span>
          <span className="shrink-0 tabular-nums text-[var(--color-text-muted)]">
            {unattached} executor{unattached === 1 ? "" : "s"}
          </span>
        </button>
      )}

      {elsewhere.length > 0 && (
        <div
          data-execution-elsewhere
          className="border-t border-[var(--color-border)] px-3 py-1 text-[10px] text-[var(--color-text-muted)]"
        >
          {elsewhere.map(({ row, declared }) => (
            <Link
              key={row.slug}
              to={rowHref(row)}
              title={`${row.name} trades on ${declared}, which this desk is not reading`}
              className="block truncate transition-colors hover:text-[var(--color-text)]"
            >
              {row.name} · trades on {declared}
            </Link>
          ))}
        </div>
      )}

      {footer}
    </div>
  );
}

/** The chevron, or the space one would have taken — so nothing shifts sideways. */
function Twisty({
  open,
  onToggle,
  label,
}: {
  open: boolean;
  onToggle: (() => void) | null;
  label: string;
}) {
  if (!onToggle) return <span className="h-3 w-3 shrink-0" />;
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <button
      type="button"
      data-execution-twisty
      aria-expanded={open}
      title={open ? `Collapse ${label}` : `Expand ${label}`}
      onClick={(e) => {
        // The row around it opens something; the chevron only unfolds it, and
        // a click that did both would navigate away from what it just revealed.
        e.stopPropagation();
        onToggle();
      }}
      className="shrink-0 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
    >
      <Icon className="h-3 w-3" />
    </button>
  );
}

/**
 * One agent, and what its records on this server add up to.
 *
 * The money is **the fold** over this row's spine, not the run rollup the
 * `["agents"]` payload carries: it is the number `/bots` prints for the same
 * scope, and ARCH-324's rule is that there is one of it. The liveness beside it
 * — the dot, the last decision, the next tick — is the run's, from `fleetRows`,
 * because none of the three is a fact about trading records at all.
 */
function AgentRow({
  row,
  live,
  nowSec,
  symbol,
  open,
  onToggle,
  onOpenAgent,
}: {
  row: ExecutionRow;
  /** The agent's own run, or `null` for an owner nothing claims. */
  live: FleetRow | null;
  nowSec: number;
  symbol: string;
  open: boolean;
  onToggle: () => void;
  onOpenAgent?: (slug: string) => void;
}) {
  const navigate = useNavigate();
  const running = live?.live?.status === "running";
  const due = dueInSec(live?.live ?? null, nowSec);
  const slug = row.agent?.slug;

  const openIt = () => {
    if (!slug) return onToggle();
    if (onOpenAgent) onOpenAgent(slug);
    else navigate(`/agents/${encodeURIComponent(slug)}`);
  };

  return (
    <div data-agent-row={row.id} className="px-3 pt-1.5">
      <div className="flex items-baseline gap-1.5">
        <Twisty open={open} onToggle={row.hasChildren ? onToggle : null} label={row.label} />
        <button
          type="button"
          data-agent-open={slug ?? ""}
          onClick={openIt}
          title={
            slug
              ? `Open ${row.label} — everything it is running on this server`
              : `${row.label} — records this fleet map does not credit to an agent`
          }
          className="flex min-w-0 flex-1 items-baseline gap-1.5 text-left transition-colors hover:text-[var(--color-text)]"
        >
          {/* Live, paused, or not looping at all — and never absent: an agent
              that has stopped is still an agent whose records are on screen. */}
          <span
            data-agent-live={running ? "" : undefined}
            className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${
              running
                ? "bg-emerald-400"
                : live?.live
                  ? "bg-amber-400"
                  : "bg-[var(--color-text-muted)]/40"
            }`}
          />
          <span className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            {row.label}
          </span>
        </button>
        <span
          className="shrink-0 font-mono tabular-nums"
          style={{ color: pnlColor(row.totals.net) }}
        >
          {formatCurrencyPnl(row.totals.net, symbol)}
        </span>
        <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-muted)]">
          {formatCompactVolume(row.totals.volume, symbol)}
        </span>
      </div>

      {live && (
        <div className="flex items-baseline gap-1.5 pl-[18px] text-[10px] text-[var(--color-text-muted)]">
          {due !== null && (
            <span
              data-agent-due
              className={`shrink-0 font-mono ${due <= 0 ? "text-amber-400" : ""}`}
            >
              {due > 0 ? `next in ${countdown(due)}` : `overdue ${countdown(-due)}`}
            </span>
          )}
          {live.lastDid ? (
            <Link
              to={decisionHref(live)}
              data-agent-decision
              title="Read the whole tick this came from"
              className={`min-w-0 truncate transition-colors hover:underline ${
                live.lastDid.ok ? "" : "text-[var(--color-red)]"
              }`}
            >
              {live.lastDid.summary}
            </Link>
          ) : (
            live.lastSaid && (
              <span data-agent-decision className="min-w-0 truncate">
                {live.lastSaid}
              </span>
            )
          )}
        </div>
      )}
    </div>
  );
}

/**
 * One bot, once per group: its whole branch in the browser, and the level the
 * executors under it are counted at.
 *
 * Drawn only when the agent above it runs more than one — a fleet running a
 * single bot must not spend a chevron saying so (see `executionTree`).
 */
function BotRow({
  row,
  open,
  onToggle,
  onOpen,
}: {
  row: ExecutionRow;
  open: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  return (
    <div className="flex items-baseline gap-1.5 px-3 pt-1">
      <Twisty open={open} onToggle={row.hasChildren ? onToggle : null} label={row.label} />
      <button
        type="button"
        onClick={onOpen}
        title={`Everything ${row.label} is running`}
        data-bot-group
        className="flex min-w-0 flex-1 items-baseline gap-2 pl-3 text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        <span className="min-w-0 flex-1 truncate">{clipBotName(row.label)}</span>
      </button>
    </div>
  );
}

/** One controller — the row this panel has always drawn, one level deeper. */
function ControllerRow({
  row,
  server,
  executors,
  now,
  convert,
  symbol,
  onOpen,
}: {
  row: ExecutionRow;
  server: string;
  executors: number;
  /**
   * The panel's clock, handed down rather than read here.
   *
   * A `Date.now()` in render is what the compiler forbids, and it is the same
   * clock the fold above measured its runtimes against — so the uptime a row
   * prints and the hours its totals were computed over cannot disagree.
   */
  now: number;
  convert: (value: number, pair: string) => number;
  symbol: string;
  onOpen: (scope: string) => void;
}) {
  const leaf: PerfLeaf = row.leaves[0];
  const money = (val: number) => convert(val, leaf.pair);
  const net = money(leaf.net);
  const realized = money(leaf.realized);
  const unrealized = money(leaf.unrealized);
  const uptime = leaf.startedAt ? (now - leaf.startedAt) / 3_600_000 : NaN;
  const scope = controllerNodeId(leaf) ?? row.id;
  const stopped = leaf.status === "stopped";

  return (
    <tr
      data-controller-row
      data-paused={stopped ? "" : undefined}
      role="button"
      tabIndex={0}
      title={`${leaf.label} on ${leaf.bot}${stopped ? " — paused" : ""}`}
      onClick={() => onOpen(scope)}
      onKeyDown={(e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        onOpen(scope);
      }}
      className={`cursor-pointer transition-colors hover:bg-[var(--color-surface-hover)] ${
        // Paused rows are dimmed rather than tinted: their numbers are still
        // true, they are just no longer moving, and a row that shouted would
        // compete with the PnL colours for the same glance.
        stopped ? "opacity-55" : ""
      }`}
    >
      {/* The column that absorbs the slack, and the only one allowed to
          truncate: two rows under the same bot are told apart by nothing else,
          so it gets every pixel the numbers do not need — and the row's `title`
          says it in full when even that is not enough. It is indented by its
          depth, which is what makes the nesting readable without a rule. */}
      <td
        className="truncate py-0.5 pr-1.5"
        style={{ paddingLeft: 12 + row.depth * 10 }}
      >
        {leaf.label}
      </td>
      <td className="whitespace-nowrap px-1.5 py-0.5 text-[var(--color-text-muted)]">
        {leaf.pair}
      </td>
      <td className="px-1.5 py-0.5 text-right font-mono tabular-nums">{executors}</td>
      <td className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
        {formatRuntimeHours(uptime)}
      </td>
      <td className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
        {formatCompactVolume(money(leaf.volume), symbol)}
      </td>
      <td
        className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono tabular-nums"
        style={{ color: pnlColor(realized) }}
      >
        {formatCurrencyPnl(realized, symbol)}
      </td>
      <td
        className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono tabular-nums"
        style={{ color: pnlColor(unrealized) }}
      >
        {formatCurrencyPnl(unrealized, symbol)}
      </td>
      <td
        className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono font-medium tabular-nums"
        style={{ color: pnlColor(net) }}
      >
        {formatCurrencyPnl(net, symbol)}
      </td>
      {/* The one cell that acts rather than reports. It stops the click from
          reaching the row, because the row navigates away and a pause that also
          left the page would look like it had done something else. */}
      <td className="py-0.5 pl-1.5 pr-3 text-right">
        <ControllerToggle
          server={server}
          bot={leaf.bot}
          controllerId={leaf.controllerId}
          stopped={stopped}
          label={leaf.label}
        />
      </td>
    </tr>
  );
}
