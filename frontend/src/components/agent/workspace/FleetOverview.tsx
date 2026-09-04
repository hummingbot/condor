import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight, Brain, Clock, Server } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  decisionHref,
  fleetRows,
  foldRows,
  foldServerOf,
  foldTargets,
  moneyHref,
  rowHref,
  sameFolds,
  strategylessAgents,
  dueInSec,
  type FleetRow,
  type FoldTarget,
  type RowFold,
} from "@/components/agent/workspace/fleet";
import type { WorkspaceAlert } from "@/components/agent/workspace/views";
import { useFleetData } from "@/hooks/useFleetData";
import { useSeconds } from "@/hooks/useSeconds";
import { useServer } from "@/hooks/useServer";
import { countdown } from "@/lib/agent-attribution";
import { api } from "@/lib/api";
import {
  formatCurrencyPnl,
  formatCurrencyVolume,
  pnlTextClass,
} from "@/lib/formatters";
import { quoteConverter, runningLeaves } from "@/lib/perf-population";

/**
 * What every agent is doing, on one screen (FEAT-104).
 *
 * A card grid of the fleet used to live at this address and was deleted on the
 * grounds that "its only unique job was showing which agents are running, and a
 * line at the top of the rail does that without a second page". That is the bar
 * here, and it is cleared by what each row carries rather than by the layout:
 * the money the agent's fleet actually made (FEAT-102), the last thing it
 * decided as a link into the tick that decided it, when it ticks next, and
 * whatever `alertsFor` (FEAT-103) says wants a person. The rail's line says
 * none of those.
 *
 * One `["agents"]` query — key and interval identical to the chat rail's, so
 * react-query dedupes and this page costs no extra request — one fleet per
 * server the fleet actually trades on, and one clock, alive only while
 * something is looping. Every judgement it makes is in `fleet.ts`; this file is
 * markup and queries.
 *
 * **The money is the fold** (ARCH-324). It used to be the run rollup, because
 * `AgentSummary` carried no server and a fold is computed over a server's
 * records — so the home showed one quantity and the Money view another, for the
 * same agent, and FEAT-109 had to ship the difference as a label. The summary
 * carries its server now, so each row folds through `reconcile` at the very
 * scope its money link opens, and the two screens print one number.
 *
 * Owns its own scrolling: `main` is full bleed on `/` under either view
 * (`lib/homeView.ts`).
 */
export function FleetOverview() {
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
    refetchInterval: 10000,
  });
  const { server: ambient } = useServer();

  // A clock only while something is looping — the countdown is the one thing on
  // this page that moves on its own, and a `Date.now()` in render is what the
  // compiler forbids anyway.
  const anyRunning = agents.some((agent) => agent.status === "running");
  const now = useSeconds(anyRunning);

  // Not memoised on purpose: `nowSec` changes every second while a loop is up,
  // so a memo over it would recompute every time it was read and cost a
  // dependency list to get wrong. Twelve agents is nothing to re-derive.
  const nowSec = now / 1000;
  const rows = fleetRows(agents, nowSec);
  const idle = strategylessAgents(agents);
  const looping = rows.filter((row) => row.live?.status === "running").length;

  /**
   * One fold per server, lifted out of the components that fetch them.
   *
   * A fleet is fetched per server and a hook cannot be called in a loop, so
   * each server gets a `ServerFold` of its own that renders nothing and reports
   * what it folded. Kept keyed by server rather than merged on arrival, so a
   * server that drops out of the list takes its rows' numbers with it instead
   * of leaving a stale fold behind under an agent that moved.
   */
  const groups = useMemo(() => foldTargets(agents, ambient), [agents, ambient]);
  const [byServer, setByServer] = useState<
    Record<string, ReadonlyMap<string, RowFold>>
  >({});
  const report = useCallback(
    (server: string, folds: ReadonlyMap<string, RowFold>) =>
      setByServer((prev) =>
        sameFolds(prev[server], folds) ? prev : { ...prev, [server]: folds },
      ),
    [],
  );
  const folds = useMemo(() => {
    const all = new Map<string, RowFold>();
    for (const { server } of groups) {
      for (const [slug, fold] of byServer[server] ?? []) all.set(slug, fold);
    }
    return all;
  }, [groups, byServer]);

  return (
    <div className="h-full min-h-0 overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-5xl">
        <header className="mb-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-lg font-semibold tracking-tight">Fleet</h1>
          <p className="text-xs text-[var(--color-text-muted)]">
            {rows.length === 0
              ? "No agent owns a strategy yet."
              : `${looping} looping of ${rows.length} agent${
                  rows.length === 1 ? "" : "s"
                } with a strategy`}
          </p>
        </header>

        {groups.map(({ server, targets }) => (
          <ServerFold
            key={server}
            server={server}
            targets={targets}
            onFold={report}
          />
        ))}

        <div className="space-y-2">
          {rows.map((row) => (
            <Row
              key={row.slug}
              row={row}
              nowSec={nowSec}
              server={foldServerOf(row, ambient)}
              fold={folds.get(row.slug) ?? null}
            />
          ))}
        </div>

        {/* A name and a "never run" — not a card, and not hidden. Hidden would
            make the home lie about how many agents this install has; a card
            would weigh a thing with no loop, no money and no decision the same
            as one that is trading. */}
        {idle.length > 0 && (
          <div className="mt-5 border-t border-[var(--color-border)] pt-3">
            <h2 className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
              No strategy yet
            </h2>
            <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
              {idle.map((agent) => (
                <li key={agent.slug} data-fleet-strategyless={agent.slug}>
                  <Link
                    to={`/agents/${encodeURIComponent(agent.slug)}`}
                    className="text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
                  >
                    {agent.name}{" "}
                    <span className="opacity-60">· never run</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}

        {agents.length === 0 && (
          <p
            data-fleet-empty
            className="py-10 text-center text-sm text-[var(--color-text-muted)]"
          >
            No agents yet.
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * One server's fleet, folded for the rows that trade on it (ARCH-324).
 *
 * Renders nothing: the rows stay in one globally ordered list, and grouping
 * them by server on screen would reorder the page around a fact the reader did
 * not ask about. What it owns is the fetch — under `useFleetData`'s own query
 * keys, so a reader who then opens `/bots` or an agent's Money view on this
 * server shares these caches rather than doubling them.
 *
 * The performance-history walk is off: this page folds and draws no chart, and
 * the walk costs a paged request per controller.
 *
 * Its clock is `useSeconds(false)` — a mount-time reading, as the Money view
 * takes it. The fold's clock feeds only the measured runtime, which no row
 * prints, and a ticking one would re-fold and re-publish every second.
 */
function ServerFold({
  server,
  targets,
  onFold,
}: {
  server: string;
  targets: readonly FoldTarget[];
  onFold: (server: string, folds: ReadonlyMap<string, RowFold>) => void;
}) {
  const fleet = useFleetData(server, { population: "running", history: false });
  const now = useSeconds(false);

  // One converter with `/bots` and the Money view: the fold converts per leaf
  // using the leaf's own quote, and two numbers that differed by an FX fallback
  // would be exactly the disagreement this closes.
  const cv = useMemo(() => quoteConverter(fleet.convert), [fleet.convert]);

  const leaves = useMemo(
    () =>
      runningLeaves({
        controllers: fleet.controllers,
        executors: fleet.executors,
        owners: fleet.owners,
        deeds: fleet.deeds,
      }),
    [fleet.controllers, fleet.executors, fleet.owners, fleet.deeds],
  );

  const folds = useMemo(
    () =>
      foldRows(targets, {
        leaves,
        deeds: fleet.deeds,
        convert: cv,
        now,
        symbol: fleet.currencySymbol ?? "$",
      }),
    [targets, leaves, fleet.deeds, cv, now, fleet.currencySymbol],
  );

  useEffect(() => onFold(server, folds), [server, folds, onFold]);

  return null;
}

function Row({
  row,
  nowSec,
  server,
  fold,
}: {
  row: FleetRow;
  nowSec: number;
  /** The server its records are folded from, `""` when nobody has said. */
  server: string;
  /** Its fold, or `null` while the server has not answered. */
  fold: RowFold | null;
}) {
  const live = row.live;
  const running = live?.status === "running";
  const due = dueInSec(live, nowSec);

  // A dash unless there is a server to fold, a fold back from it, and something
  // in it worth stating. `$0.00` for money that is merely unattributed is the
  // one thing this column is not allowed to print (FEAT-109's rule, ARCH-324's
  // new way to break it).
  const money = server && fold?.reported ? fold : null;

  return (
    <div
      data-fleet-row={row.slug}
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
            running
              ? "bg-emerald-400"
              : live
                ? "bg-amber-400"
                : "bg-[var(--color-text-muted)]/40"
          }`}
        />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Link
              to={rowHref(row)}
              className="group flex items-center gap-1 text-sm font-semibold text-[var(--color-text)] transition-colors hover:text-[var(--color-primary)]"
            >
              {row.name}
              <ArrowUpRight className="h-3 w-3 opacity-0 transition-opacity group-hover:opacity-100" />
            </Link>
            {row.strategy && (
              <span className="font-mono text-[11px] text-[var(--color-text-muted)]">
                {row.strategy.name}
              </span>
            )}
            {row.agentKey && <Chip icon={Brain} text={row.agentKey} />}
            {(row.serverName || server) && (
              <Chip icon={Server} text={row.serverName || server} />
            )}
          </div>

          {/* When it goes again — the fact the rail's live line cannot say. */}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] text-[var(--color-text-muted)]">
            <Clock className="h-3 w-3 shrink-0" />
            {live ? (
              <>
                <span className="font-mono">tick {live.tick_count}</span>
                {due !== null && (
                  <span
                    data-fleet-due
                    className={`font-mono ${due <= 0 ? "text-amber-400" : ""}`}
                  >
                    ·{" "}
                    {due > 0
                      ? `next in ${countdown(due)}`
                      : `overdue ${countdown(-due)}`}
                  </span>
                )}
                {!running && <span>· {live.status}</span>}
              </>
            ) : row.sessionCount > 0 ? (
              <span>
                Not looping · {row.sessionCount} run
                {row.sessionCount === 1 ? "" : "s"} on record
              </span>
            ) : (
              <span>Never run</span>
            )}
          </div>

          {/* What it last decided, and the address of the tick that decided it.
              Only a live loop reports its deeds in this payload, so an idle
              agent says so rather than showing a stale line from a run the
              overview has not read. */}
          <p data-fleet-decision className="mt-2 text-xs leading-relaxed">
            {row.lastDid ? (
              <Link
                to={decisionHref(row)}
                className={`transition-colors hover:underline ${
                  row.lastDid.ok
                    ? "text-[var(--color-text)]"
                    : "text-[var(--color-red)]"
                }`}
                title="Read the whole tick this came from"
              >
                {row.lastDid.summary}
                {row.lastDid.tick > 0 && (
                  <span className="ml-1.5 font-mono text-[10px] text-[var(--color-text-muted)]">
                    #{row.lastDid.tick}
                  </span>
                )}
              </Link>
            ) : row.lastSaid ? (
              <span className="text-[var(--color-text)]">{row.lastSaid}</span>
            ) : (
              <span className="text-[var(--color-text-muted)]">
                {row.sessionCount > 0
                  ? "Nothing decided since this loop started."
                  : "It has not decided anything yet."}
              </span>
            )}
          </p>

          {row.alerts.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {row.alerts.map((alert) => (
                <AlertChip key={alert.kind} row={row} alert={alert} />
              ))}
            </div>
          )}
        </div>

        {/* The fold, at the scope this link opens (ARCH-324) — the same
            computation over the same records as the Money view's headline, so
            the two screens print one number for one agent rather than two
            correct ones that look like a contradiction.

            A dash wherever it cannot be said: no server has been declared for
            this agent anywhere, or the server has not answered yet, or its
            records say nothing at all. `$0.00` would read as "it traded and
            broke even", which is a different statement and not this one. */}
        <Link
          to={moneyHref(row)}
          data-fleet-money
          className="group shrink-0 text-right"
          title={
            server
              ? "Every record this agent owns, folded as it stands now — the number the Money view leads with"
              : "Nobody has said which server this agent trades on, so its records cannot be folded"
          }
        >
          <div
            data-fleet-net
            className={`font-mono text-sm font-semibold ${
              money === null ? "text-[var(--color-text-muted)]" : pnlTextClass(money.net)
            }`}
          >
            {money === null ? "—" : formatCurrencyPnl(money.net, money.symbol)}
          </div>
          <div
            data-fleet-volume
            className="font-mono text-[11px] text-[var(--color-text-muted)]"
          >
            {money === null
              ? "—"
              : formatCurrencyVolume(money.volume, money.symbol)}{" "}
            vol
          </div>
          <div
            data-fleet-money-label
            className="text-[10px] text-[var(--color-text-muted)] transition-colors group-hover:text-[var(--color-primary)]"
          >
            {server ? "what its records show" : "no server to fold"}
          </div>
        </Link>
      </div>
    </div>
  );
}

function Chip({
  icon: Icon,
  text,
}: {
  icon: typeof Brain;
  text: string;
}) {
  return (
    <span className="flex items-center gap-1 rounded border border-[var(--color-border)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-muted)]">
      <Icon className="h-2.5 w-2.5 shrink-0" /> {text}
    </span>
  );
}

function AlertChip({
  row,
  alert,
}: {
  row: FleetRow;
  alert: WorkspaceAlert;
}) {
  const failed = alert.kind === "failed";
  const to =
    alert.tick !== undefined && row.strategy
      ? `/agents/${encodeURIComponent(row.slug)}?view=tick&strategy=${encodeURIComponent(
          row.strategy.slug,
        )}&tick=${alert.tick}`
      : rowHref(row);

  return (
    <Link
      to={to}
      data-fleet-alert={alert.kind}
      className={`flex items-start gap-1.5 rounded border px-2 py-1 text-[11px] transition-opacity hover:opacity-80 ${
        failed
          ? "border-red-500/30 bg-red-500/5 text-red-300"
          : "border-amber-500/30 bg-amber-500/5 text-amber-300"
      }`}
    >
      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
      <span>{alert.text}</span>
    </Link>
  );
}
