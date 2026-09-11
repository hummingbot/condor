import { AlertTriangle, FlaskConical, MessageSquare, Send, Zap } from "lucide-react";
import { useState } from "react";

import { MODE_STYLES } from "@/components/agent/modeStyles";
import { isLiveRun, isLoopRun, runFacts, runLabel } from "@/components/agent/lab/runs";
import { useSeconds } from "@/hooks/useSeconds";
import type { AgentRunRow } from "@/lib/api";
import { formatAge } from "@/lib/formatters";

/**
 * The three readings of "what has this agent been doing".
 *
 * On an agent that is mostly chatted with, the loop runs are the needles — and
 * on one that mostly loops, a chat is. `all` stays the default because the
 * whole point of the union is that you do not have to know which kind a piece
 * of work was filed under before you can look for it.
 */
const KIND_FILTERS = [
  { id: "all", label: "all", kinds: null },
  { id: "loops", label: "loops", kinds: ["session", "experiment"] },
  { id: "chats", label: "chats", kinds: ["conversation"] },
  { id: "tasks", label: "tasks", kinds: ["delegation"] },
] as const;

type KindFilterId = (typeof KIND_FILTERS)[number]["id"];

/**
 * Every stretch of work this agent ever did, in one column.
 *
 * The taxonomy is `SessionReviewer`'s — sessions and experiments folded into a
 * single list — lifted out of an overlay that had no URL and given a rail on a
 * page that does. Two things change with the move. Dry runs are **peers** here
 * rather than a header button that opened only the latest one; and the rail
 * carries no PnL, because pricing a run is a per-session backend fan-out and a
 * rail that polls every five seconds would either be slow or be lying. What a
 * row says instead is what a run actually is: how many ticks, for how long,
 * and whether it broke.
 *
 * Since FEAT-111 it lists two more kinds — a conversation and a delegation —
 * which is what makes the rail answer "what has this agent been doing" for an
 * agent that owns no strategy at all. Those rows carry a title instead of a
 * strategy name, because "S3 · brl mm" reads for a loop run and nothing reads
 * for a chat.
 *
 * Two chip rows, and they answer different questions. The first scopes the list
 * to one strategy, the same shape the `/routines` sidebar uses for its agent
 * chips — comparing a strategy's runs against its sibling's is a thing the
 * strategy page could never do. The second scopes it to one *kind*, because on
 * an agent that is mostly chatted with the loop runs are the needles.
 */
export function RunRail({
  runs,
  strategyFilter,
  onStrategyFilter,
  selectedKey,
  onSelectRun,
  isLoading = false,
  hasMore = false,
  onShowMore,
}: {
  runs: AgentRunRow[];
  /** The selected strategy slug, or `null` for all of them. */
  strategyFilter: string | null;
  onStrategyFilter: (sslug: string | null) => void;
  /** `"{strategy_slug}:{run_id}"` — unique across strategies, unlike `run_id`. */
  selectedKey: string | null;
  onSelectRun: (run: AgentRunRow) => void;
  isLoading?: boolean;
  /** Whether the server had more than this window to give. */
  hasMore?: boolean;
  /** Widen the window. Absent when the caller does not page. */
  onShowMore?: () => void;
}) {
  // A view preference, not an address: `?run=` is what a link has to carry, and
  // a chip that only changes which rows are on screen does not earn a
  // parameter — the same line `MODE_STYLES` and the tick spine draw.
  const [kindFilter, setKindFilter] = useState<KindFilterId>("all");
  // One clock for the whole rail: a live run's duration counts up, and two
  // intervals started a frame apart show the same run a second out from itself.
  const anyLive = runs.some(isLiveRun);
  const now = useSeconds(anyLive);
  const nowSec = now / 1000;

  // Strategy order follows the runs, so the chip list is newest-active first
  // and a strategy that has never run does not claim a chip. A chat has no
  // strategy, so it claims none either — an empty chip would filter to nothing
  // a reader could name.
  const strategies: { slug: string; name: string }[] = [];
  for (const run of runs) {
    if (!run.strategy_slug) continue;
    if (!strategies.some((s) => s.slug === run.strategy_slug)) {
      strategies.push({ slug: run.strategy_slug, name: run.strategy_name });
    }
  }

  // Only the chips that would select something: an agent with no chats does not
  // get to be told it has none, and an agent with only chats gets no chip row.
  const presentKinds = new Set(runs.map((r) => r.kind));
  const kindChips = KIND_FILTERS.filter(
    (f) => !f.kinds || f.kinds.some((k) => presentKinds.has(k)),
  );
  const activeKinds = KIND_FILTERS.find((f) => f.id === kindFilter)?.kinds ?? null;

  // Filtering to a strategy is filtering to the loop, so the strategy-less
  // kinds come off with it rather than sitting under a heading they are not in.
  const visible = runs.filter((r) => {
    if (strategyFilter && r.strategy_slug !== strategyFilter) return false;
    if (activeKinds && !activeKinds.some((k) => k === r.kind)) return false;
    return true;
  });
  const anyChats = presentKinds.has("conversation");

  return (
    <div className="flex h-full w-[260px] shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="border-b border-[var(--color-border)] px-3 py-2.5">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
          Runs
        </span>
      </div>

      {strategies.length > 1 && (
        <div className="flex flex-wrap gap-1 border-b border-[var(--color-border)]/60 px-2 py-2">
          <Chip
            label="all"
            active={strategyFilter === null}
            onClick={() => onStrategyFilter(null)}
          />
          {strategies.map((s) => (
            <Chip
              key={s.slug}
              label={s.name}
              active={strategyFilter === s.slug}
              onClick={() => onStrategyFilter(s.slug)}
            />
          ))}
        </div>
      )}

      {kindChips.length > 2 && (
        <div
          className="flex flex-wrap gap-1 border-b border-[var(--color-border)]/60 px-2 py-2"
          data-run-kind-chips
        >
          {kindChips.map((f) => (
            <Chip
              key={f.id}
              label={f.label}
              active={kindFilter === f.id}
              onClick={() => setKindFilter(f.id)}
            />
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {visible.length === 0 ? (
          <p className="px-3 py-8 text-center text-xs text-[var(--color-text-muted)]">
            {isLoading ? "Loading runs…" : "No runs yet."}
          </p>
        ) : (
          visible.map((run) => (
            <RunRow
              key={`${run.strategy_slug}:${run.run_id}`}
              run={run}
              active={selectedKey === `${run.strategy_slug}:${run.run_id}`}
              nowSec={nowSec}
              onClick={() => onSelectRun(run)}
            />
          ))
        )}

        {hasMore && onShowMore && (
          <button
            type="button"
            data-run-show-more
            onClick={onShowMore}
            className="w-full px-3 py-2.5 text-center text-[11px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            Show older
          </button>
        )}
      </div>

      {/* Two asymmetries that read as data loss if they are not said out loud.
          A conversation is private, so two people see different rails for the
          same agent — correct, and invisible without this line. And a chat is
          kept for less time than a session's directory is, so the rail shows a
          shrinking tail beside a permanent one. */}
      {anyChats && (
        <p className="border-t border-[var(--color-border)]/60 px-3 py-2 text-[10px] leading-snug text-[var(--color-text-muted)]/70">
          Chats are yours alone, and are kept for less time than a loop's runs.
        </p>
      )}
    </div>
  );
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
        active
          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      {label}
    </button>
  );
}

/**
 * The kind mark: a dot for a session, an icon for everything else.
 *
 * Filled = live, hollow = closed. The experiment icons and colours are the ones
 * `MODE_STYLES` already assigns, so a dry run reads the same here as it does on
 * a badge elsewhere. A chat and a background task get an icon each, on the same
 * axis and for the same reason: a stretch of work you can open is worth telling
 * apart at a glance from one that could have moved money.
 */
function KindMark({ run }: { run: AgentRunRow }) {
  if (run.kind === "conversation") {
    return <MessageSquare className="h-3 w-3 shrink-0 text-sky-400" />;
  }
  if (run.kind === "delegation") {
    return (
      <Send
        className={`h-3 w-3 shrink-0 ${run.error ? "text-red-400" : "text-violet-400"}`}
      />
    );
  }
  if (run.kind === "experiment") {
    const Icon = run.execution_mode === "run_once" ? Zap : FlaskConical;
    const color =
      MODE_STYLES[run.execution_mode]?.text ?? "text-amber-400";
    return <Icon className={`h-3 w-3 shrink-0 ${run.error ? "text-red-400" : color}`} />;
  }
  const live = isLiveRun(run);
  return (
    <span
      className={`h-2 w-2 shrink-0 rounded-full ${
        live
          ? "bg-emerald-400"
          : "border border-[var(--color-text-muted)]/50 bg-transparent"
      }`}
    />
  );
}

function RunRow({
  run,
  active,
  nowSec,
  onClick,
}: {
  run: AgentRunRow;
  active: boolean;
  nowSec: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-run-row={`${run.strategy_slug}:${run.run_id}`}
      data-run-active={active ? "true" : "false"}
      onClick={onClick}
      className={`w-full px-3 py-2.5 text-left transition-all ${
        active
          ? "border-l-2 border-l-[var(--color-primary)] bg-[var(--color-primary)]/5"
          : "border-l-2 border-l-transparent hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      <div className="flex items-center gap-1.5">
        <KindMark run={run} />
        <span
          className={`font-mono text-xs font-bold ${
            active ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"
          }`}
        >
          {runLabel(run)}
        </span>
        {/* A loop run is named by its playbook; the two kinds that have none
            are named by what they were about. An untitled chat says so rather
            than leaving the row anonymous. */}
        <span className="truncate text-[11px] text-[var(--color-text-muted)]">
          {isLoopRun(run.kind)
            ? run.strategy_name
            : run.title || (run.kind === "conversation" ? "Untitled chat" : "Task")}
        </span>
        {run.error && (
          <span className="ml-auto flex shrink-0 items-center gap-0.5 rounded bg-red-500/15 px-1 py-0.5 text-[8px] font-bold uppercase text-red-400">
            <AlertTriangle className="h-2.5 w-2.5" />
            failed
          </span>
        )}
      </div>
      <div className="mt-0.5 flex items-center gap-1.5 pl-[18px] text-[10px] text-[var(--color-text-muted)]/70">
        <span>{run.started_at ? `${formatAge(run.started_at)} ago` : "—"}</span>
        <span className="opacity-40">·</span>
        <span className="font-mono">{runFacts(run, nowSec)}</span>
      </div>
    </button>
  );
}
