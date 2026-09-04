import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";

import type { FloorModel, FloorMoney } from "@/components/agent/floor/floor";
import { seriesColor } from "@/components/agent/floor/FloorChart";
import { ownerDataKey, type FloorChartRow } from "@/lib/owner-series";
import {
  formatAge,
  formatCurrencyPnl,
  formatCurrencyVolume,
  pnlTextClass,
} from "@/lib/formatters";

/**
 * One row per agent — the agent entire, over every server it declares.
 *
 * The relationship to the home's row is **stated, not assumed**: this row is
 * the agent, the home's row is one strategy of it, and for an agent with a
 * single strategy in scope they are the same number. The link goes to
 * `?view=money`, which is the screen that reconciles the two (FEAT-109).
 *
 * The order is `attributedMoney`'s (`fleet.ts:137-148`), decided one level up
 * and for its reason: the rollup arrives with `["agents"]` while a fold arrives
 * per server, one answer at a time, so ranking on the fold would reorder the
 * list under the reader's cursor as each server replied.
 *
 * Then the named non-agent parts. They are not an "other" bucket: each is a set
 * of records with an address, and a residual — an attributed run key no listed
 * agent claims, which happens when an agent is deleted while its bots go on
 * trading — gets a lead into exactly its records. Anything that cannot be named
 * stays named rather than filed under "other" (`reconcile.ts:103-148`).
 *
 * The liveness column is the honest one: open positions, running leaves and the
 * age of the last close. Live order counts have no source in this app at all.
 */
export function FloorRows({
  model,
  rows,
}: {
  model: FloorModel;
  /** The merged chart rows, for the sparklines. Empty until history lands. */
  rows: readonly FloorChartRow[];
}) {
  const spark = (key: string) => sparkPoints(rows, key);

  return (
    <section
      data-floor-rows
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
    >
      <header className="border-b border-[var(--color-border)] px-3 py-1.5">
        <h2 className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          By agent
        </h2>
      </header>

      {model.rows.length === 0 && model.others.length === 0 ? (
        <p
          data-floor-empty
          className="px-3 py-8 text-center text-sm text-[var(--color-text-muted)]"
        >
          No records folded yet.
        </p>
      ) : (
        <ul className="divide-y divide-[var(--color-border)]">
          {model.rows.map((row, index) => (
            <Row
              key={row.slug}
              test={row.slug}
              name={row.name}
              href={`/agents/${encodeURIComponent(row.slug)}?view=money`}
              hint="Every record this agent owns, folded as it stands now"
              money={row}
              reported={row.reported}
              symbol={model.symbol}
              color={seriesColor(index)}
              points={spark(row.slug)}
            />
          ))}
          {model.others.map((other, index) => (
            <Row
              key={other.key}
              test={other.key}
              name={other.label}
              href={`/bots?scope=${encodeURIComponent(other.scope)}`}
              hint={
                other.kind === "residual"
                  ? "These records are attributed to a run key no listed agent claims — open them"
                  : "Records the fleet map could not credit to any agent"
              }
              lead={other.kind === "residual"}
              money={other}
              reported={
                other.totals.volume > 0 ||
                other.totals.net !== 0 ||
                other.totals.positions > 0
              }
              symbol={model.symbol}
              color={seriesColor(model.rows.length + index)}
              points={spark(other.key)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function Row({
  test,
  name,
  href,
  hint,
  lead = false,
  money,
  reported,
  symbol,
  color,
  points,
}: {
  test: string;
  name: string;
  href: string;
  hint: string;
  lead?: boolean;
  money: FloorMoney;
  reported: boolean;
  symbol: string;
  color: string;
  points: number[];
}) {
  const t = money.totals;
  return (
    <li
      data-floor-row={test}
      className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 px-3 py-2 sm:grid-cols-[minmax(0,1.4fr)_72px_repeat(3,minmax(0,1fr))_auto]"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          aria-hidden="true"
          className="h-2.5 w-1 shrink-0 rounded-sm"
          style={{ background: color }}
        />
        <Link
          to={href}
          title={hint}
          className="group flex min-w-0 items-center gap-1 text-sm font-medium transition-colors hover:text-[var(--color-primary)]"
        >
          <span className="truncate">{name}</span>
          <ArrowUpRight className="h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
        </Link>
        {lead && (
          <span
            data-floor-lead
            className="shrink-0 rounded bg-[var(--color-yellow)]/15 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-[var(--color-yellow)]"
          >
            lead
          </span>
        )}
      </div>

      <Spark points={points} color={color} />

      <Cell
        test="net"
        value={reported ? formatCurrencyPnl(t.net, symbol) : "—"}
        tone={reported ? pnlTextClass(t.net) : "text-[var(--color-text-muted)]"}
        caption="net"
      />
      <Cell
        test="volume"
        value={reported ? formatCurrencyVolume(t.volume, symbol) : "—"}
        caption="volume"
      />
      <Cell
        test="exposure"
        value={
          t.positions > 0 ? formatCurrencyPnl(money.exposure, symbol) : "—"
        }
        tone={
          t.positions > 0
            ? pnlTextClass(money.exposure)
            : "text-[var(--color-text-muted)]"
        }
        caption={`${t.positions} open`}
      />
      <Cell
        test="live"
        value={`${money.running}`}
        caption={
          money.lastClose !== null
            ? `last close ${formatAge(money.lastClose)}`
            : "running"
        }
      />
    </li>
  );
}

function Cell({
  test,
  value,
  caption,
  tone = "",
}: {
  test: string;
  value: string;
  caption: string;
  tone?: string;
}) {
  return (
    <div className="text-right">
      <span
        data-floor-cell={test}
        className={`block font-mono text-xs font-semibold ${tone}`}
      >
        {value}
      </span>
      <span className="block text-[9px] text-[var(--color-text-muted)]">
        {caption}
      </span>
    </div>
  );
}

/** One owner's line out of the merged rows — the values it actually carries. */
export function sparkPoints(
  rows: readonly FloorChartRow[],
  key: string,
): number[] {
  const field = ownerDataKey(key);
  const out: number[] = [];
  for (const row of rows) {
    const value = row[field];
    if (typeof value === "number" && Number.isFinite(value)) out.push(value);
  }
  return out;
}

/**
 * The row's own shape, at row scale.
 *
 * Drawn from the same series the chart above draws, so a row and its line say
 * the same thing — and drawn with the same colour, which is what ties the two
 * together on a page where colour is the only channel that can.
 *
 * Nothing at all when there is no line: a sparkline of one point is a dot that
 * reads as data, and a flat line through zero would say the agent broke even.
 */
function Spark({ points, color }: { points: number[]; color: string }) {
  if (points.length < 2) {
    return <span className="hidden text-[10px] text-[var(--color-text-muted)] sm:block" />;
  }
  let min = points[0];
  let max = points[0];
  for (const value of points) {
    if (value < min) min = value;
    if (value > max) max = value;
  }
  const span = max - min || 1;
  const step = 100 / (points.length - 1);
  const d = points
    .map((value, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(2)},${(16 - ((value - min) / span) * 14 - 1).toFixed(2)}`)
    .join(" ");

  return (
    <svg
      data-floor-spark
      viewBox="0 0 100 16"
      preserveAspectRatio="none"
      className="hidden h-4 w-[72px] sm:block"
      aria-hidden="true"
    >
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
