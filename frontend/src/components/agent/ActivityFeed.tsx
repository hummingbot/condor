import { useQuery } from "@tanstack/react-query";
import {
  Loader2,
  MessageCircleQuestion,
  Send,
  SquareTerminal,
  Wrench,
} from "lucide-react";
import { type ReactNode, useState } from "react";

import { CodeRunSheet } from "@/components/agent/CodeRunSheet";
import { DelegationSheet } from "@/components/agent/DelegationSheet";
import {
  DELEGATION_STATUS,
  formatDelegationTime,
} from "@/components/agent/delegationStatus";
import { api, type DelegationKind, type DelegationSummary } from "@/lib/api";

/** How a kind reads in a row: its glyph and what to call it. */
const KIND: Record<DelegationKind, { icon: typeof Send; label: string }> = {
  delegate: { icon: Send, label: "background" },
  consult: { icon: MessageCircleQuestion, label: "consult" },
  code: { icon: SquareTerminal, label: "code" },
};

/**
 * The channels a reader can narrow to, in the order they are worth scanning.
 *
 * The filter exists because a code run is a tool call *inside* another run: one
 * chatty consult can fire twenty snippets, and a feed where the cheap plentiful
 * kind buries the expensive rare one has stopped answering its own question
 * (FEAT-061).
 */
const FILTERS: { id: DelegationKind | undefined; label: string }[] = [
  { id: undefined, label: "All" },
  { id: "delegate", label: "Background" },
  { id: "consult", label: "Consults" },
  { id: "code", label: "Code" },
];

function kindOf(d: DelegationSummary): DelegationKind {
  // A record written before kinds existed was a delegation, because that is the
  // only thing that wrote one.
  return d.kind ?? "delegate";
}

/** A duration in the same compact units the elapsed time uses. */
function compactSecs(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.round(secs / 60)}m`;
  return `${(secs / 3600).toFixed(1)}h`;
}

/**
 * How long a code run took, from the duration the store actually measured.
 *
 * Sub-second is the common case for a snippet, so this one goes down to
 * milliseconds where `compactSecs` would round a real 340ms run to "0s".
 */
function codeDuration(d: DelegationSummary): string {
  const secs = (d.ended_at ?? 0) - (d.started_at ?? 0);
  if (secs <= 0) return "";
  return secs < 1 ? `${Math.round(secs * 1000)}ms` : compactSecs(secs);
}

/**
 * What the rows on screen add up to — computed from those rows and nothing else.
 *
 * No aggregate endpoint and no cache: this is the page the feed already
 * fetched, so the caption says which sample it measured rather than implying it
 * speaks for the whole history. A share and a median are only reported once
 * something finished; with nothing to measure they are left out instead of
 * printed as 0.
 */
function summarize(rows: DelegationSummary[]) {
  const finished = rows.filter((d) => d.status !== "running");
  const durations = finished
    .map((d) => (d.ended_at ?? 0) - (d.started_at ?? 0))
    .filter((secs) => secs > 0)
    .sort((a, b) => a - b);

  return {
    total: rows.length,
    doneShare: finished.length
      ? Math.round(
          (finished.filter((d) => d.status === "done").length / finished.length) * 100,
        )
      : null,
    median: durations.length
      ? compactSecs(durations[Math.floor(durations.length / 2)])
      : null,
  };
}

/** What "nothing here" means, in the words of whatever is being asked for. */
function emptyMessage(kind: DelegationKind | undefined, agent?: string): string {
  if (kind === "delegate")
    return agent
      ? "This agent has never been delegated a task."
      : "No delegated tasks recorded yet.";
  if (kind === "consult")
    return agent
      ? "This agent has not been consulted yet."
      : "No consults recorded yet.";
  // A code run is also the one kind a reader can be unable to see at all, so the
  // message names the possibility rather than implying the agent never computed.
  if (kind === "code")
    return agent
      ? "This agent has run no code you can see."
      : "No code runs you can see.";
  return agent ? "This agent has not run yet." : "No agent runs recorded yet.";
}

/**
 * Every run an agent performed, newest first — background tasks, consults and
 * the snippets it ran.
 *
 * Before FEAT-058 this listed delegations only, which on a typical install is a
 * handful of rows while the same agent had answered hundreds of consults that
 * were never recorded anywhere. FEAT-061 added the third kind, which was being
 * persisted in full and read by nobody. A row opens into the sheet its kind
 * deserves.
 *
 * `kind` pins the feed to one channel — the chat dock passes `"delegate"`,
 * because it is about background tasks for this conversation and every consult
 * the conversation made would drown the thing it exists to show. A pinned feed
 * shows no filter row: the pin is the answer, not a default the reader may
 * change. `agent` scopes it to one agent's page; without it the feed is
 * fleet-wide.
 */
export function ActivityFeed({
  agent,
  kind,
}: {
  agent?: string;
  kind?: DelegationKind;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [filter, setFilter] = useState<DelegationKind | undefined>(undefined);
  // A pin wins over the filter, and hides it: the dock asks for one channel.
  const active = kind ?? filter;

  const { data, isLoading, error } = useQuery({
    queryKey: ["delegation-history", agent ?? "all", active ?? "all"],
    queryFn: () => api.getDelegationHistory(agent, 100, active),
    // A finished run never changes; only a live one does.
    refetchInterval: (q) =>
      q.state.data?.delegations.some((d) => d.status === "running") ? 5000 : false,
  });

  const rows = data?.delegations ?? [];
  const open = rows.find((d) => d.task_id === openId);
  const summary = summarize(rows);

  // Filtering server-side rather than over the fetched page is the honest
  // choice: narrowing a hundred mixed rows client-side would show three
  // consults and imply that is all there ever were — the same dishonesty the
  // summary caption exists to avoid.
  const filters = kind ? null : (
    <div
      data-activity-filters
      className="flex items-center gap-1 px-1 pb-2 text-[10px]"
    >
      {FILTERS.map((f) => (
        <button
          key={f.label}
          type="button"
          data-activity-filter={f.id ?? "all"}
          data-active={active === f.id ? "" : undefined}
          onClick={() => setFilter(f.id)}
          className={`rounded-md px-2 py-1 font-medium transition-colors ${
            active === f.id
              ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
              : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          }`}
        >
          {f.label}
        </button>
      ))}
    </div>
  );

  let body: ReactNode;
  if (isLoading) {
    body = (
      <div className="flex items-center gap-2 px-1 py-3 text-xs text-[var(--color-text-muted)]">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading history…
      </div>
    );
  } else if (error) {
    body = (
      <p className="px-1 py-3 text-xs text-red-400">Could not load the history.</p>
    );
  } else if (rows.length === 0) {
    body = (
      <p className="px-1 py-3 text-xs text-[var(--color-text-muted)]">
        {emptyMessage(active, agent)}
      </p>
    );
  } else {
    body = (
      <>
        <div
          data-activity-summary
          className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 pb-2 text-[10px] text-[var(--color-text-muted)]"
        >
          <span>
            {summary.total} {summary.total === 1 ? "run" : "runs"}
          </span>
          {summary.doneShare !== null && <span>· {summary.doneShare}% done</span>}
          {summary.median && <span>· median {summary.median}</span>}
          {/* The number labels the window it actually measured, never the whole
              history: this is the page that was fetched, and there may be more. */}
          <span className="ml-auto">last {summary.total} shown</span>
        </div>

        <div className="divide-y divide-[var(--color-border)]/40">
          {rows.map((d) => {
            const s = DELEGATION_STATUS[d.status];
            const k = kindOf(d);
            const KindIcon = KIND[k].icon;
            return (
              <button
                key={d.task_id}
                type="button"
                data-activity-row={k}
                onClick={() => setOpenId(d.task_id)}
                className="flex w-full items-start gap-3 px-1 py-2 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
                title={d.task}
              >
                <span
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${s.dot} ${
                    d.status === "running" ? "animate-pulse" : ""
                  }`}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <KindIcon
                      className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]"
                      aria-label={KIND[k].label}
                    />
                    <span className="min-w-0 truncate text-xs text-[var(--color-text)]">
                      {d.task.split("\n")[0] || "—"}
                    </span>
                  </span>
                  <span className="mt-0.5 flex items-center gap-2 text-[10px] text-[var(--color-text-muted)]">
                    {!agent && (
                      <span className="font-mono text-[var(--color-primary)]">
                        {d.agent}
                      </span>
                    )}
                    {/* Each kind says the one thing it actually knows: who asked
                        for a consult, how much work a background task did, how
                        long a snippet took. No column is invented for the kind
                        that has no answer. */}
                    <span>
                      {k === "consult"
                        ? d.caller
                          ? `asked by ${d.caller}`
                          : "asked by you"
                        : KIND[k].label}
                    </span>
                    <span className={s.text}>{s.label.toLowerCase()}</span>
                    {formatDelegationTime(d) && <span>{formatDelegationTime(d)}</span>}
                    {k === "delegate" && !!d.tool_count && (
                      <span data-tool-count className="flex items-center gap-1">
                        <Wrench className="h-2.5 w-2.5" />
                        {d.tool_count}
                      </span>
                    )}
                    {k === "code" && codeDuration(d) && (
                      <span data-code-duration>{codeDuration(d)}</span>
                    )}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </>
    );
  }

  return (
    <>
      {filters}
      {body}
      {open &&
        (kindOf(open) === "code" ? (
          <CodeRunSheet row={open} onClose={() => setOpenId(null)} />
        ) : (
          <DelegationSheet task={open} onClose={() => setOpenId(null)} />
        ))}
    </>
  );
}
