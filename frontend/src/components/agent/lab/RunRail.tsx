import { AlertTriangle, FlaskConical, Zap } from "lucide-react";

import { MODE_STYLES } from "@/components/agent/modeStyles";
import { isLiveRun, runFacts, runLabel } from "@/components/agent/lab/runs";
import { useSeconds } from "@/hooks/useSeconds";
import type { AgentRunRow } from "@/lib/api";
import { formatAge } from "@/lib/formatters";

/**
 * Every run this agent ever had, in one column.
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
 * Chips on top scope the list to one strategy, the same shape the `/routines`
 * sidebar uses for its agent chips. `all` is the default because comparing a
 * strategy's runs against its sibling's is a thing the strategy page could
 * never do.
 */
export function RunRail({
  runs,
  strategyFilter,
  onStrategyFilter,
  selectedKey,
  onSelectRun,
  isLoading = false,
}: {
  runs: AgentRunRow[];
  /** The selected strategy slug, or `null` for all of them. */
  strategyFilter: string | null;
  onStrategyFilter: (sslug: string | null) => void;
  /** `"{strategy_slug}:{run_id}"` — unique across strategies, unlike `run_id`. */
  selectedKey: string | null;
  onSelectRun: (run: AgentRunRow) => void;
  isLoading?: boolean;
}) {
  // One clock for the whole rail: a live run's duration counts up, and two
  // intervals started a frame apart show the same run a second out from itself.
  const anyLive = runs.some(isLiveRun);
  const now = useSeconds(anyLive);
  const nowSec = now / 1000;

  // Strategy order follows the runs, so the chip list is newest-active first
  // and a strategy that has never run does not claim a chip.
  const strategies: { slug: string; name: string }[] = [];
  for (const run of runs) {
    if (!strategies.some((s) => s.slug === run.strategy_slug)) {
      strategies.push({ slug: run.strategy_slug, name: run.strategy_name });
    }
  }

  const visible = strategyFilter
    ? runs.filter((r) => r.strategy_slug === strategyFilter)
    : runs;

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
      </div>
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
 * The kind mark: a dot for a session, an icon for an experiment.
 *
 * Filled = live, hollow = closed. The experiment icons and colours are the ones
 * `MODE_STYLES` already assigns, so a dry run reads the same here as it does on
 * a badge elsewhere.
 */
function KindMark({ run }: { run: AgentRunRow }) {
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
        <span className="truncate text-[11px] text-[var(--color-text-muted)]">
          {run.strategy_name}
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
