import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight, Brain, Clock, Server } from "lucide-react";
import { Link } from "react-router-dom";

import {
  decisionHref,
  fleetRows,
  moneyHref,
  rowHref,
  strategylessAgents,
  dueInSec,
  type FleetRow,
} from "@/components/agent/workspace/fleet";
import type { WorkspaceAlert } from "@/components/agent/workspace/views";
import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import { api } from "@/lib/api";
import {
  formatCurrencyPnl,
  formatCurrencyVolume,
  pnlTextClass,
} from "@/lib/formatters";

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
 * react-query dedupes and this page costs no extra request — and one clock,
 * alive only while something is looping. Every judgement it makes is in
 * `fleet.ts`; this file is markup and a query.
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

        <div className="space-y-2">
          {rows.map((row) => (
            <Row key={row.slug} row={row} nowSec={nowSec} />
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

function Row({ row, nowSec }: { row: FleetRow; nowSec: number }) {
  const live = row.live;
  const running = live?.status === "running";
  const due = dueInSec(live, nowSec);

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
            {row.serverName && <Chip icon={Server} text={row.serverName} />}
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

        {/* Attributed, not aggregate: a dash where a run has claimed nothing,
            because `$0.00` and "nothing to report" are different statements and
            only one of them is true.

            And *named* (FEAT-109): this is what the agent's runs earned, which
            is not the same quantity as the fold the fleet page prints for the
            same agent. Both are correct; shown as a bare number this column
            would read as a contradiction, so it says which one it is and links
            to the screen where the two are reconciled. */}
        <Link
          to={moneyHref(row)}
          data-fleet-money
          className="group shrink-0 text-right"
          title="What its runs earned — its records show a different number, reconciled on the Money view"
        >
          <div
            data-fleet-net
            className={`font-mono text-sm font-semibold ${
              row.net === null ? "text-[var(--color-text-muted)]" : pnlTextClass(row.net)
            }`}
          >
            {row.net === null ? "—" : formatCurrencyPnl(row.net)}
          </div>
          <div
            data-fleet-volume
            className="font-mono text-[11px] text-[var(--color-text-muted)]"
          >
            {row.volume === null ? "—" : formatCurrencyVolume(row.volume)}{" "}
            vol
          </div>
          <div className="text-[10px] text-[var(--color-text-muted)] transition-colors group-hover:text-[var(--color-primary)]">
            its runs earned
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
