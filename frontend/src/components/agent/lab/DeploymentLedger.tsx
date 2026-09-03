import { ExternalLink, Package } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import {
  fleetHref,
  formatTick,
  kindIcon,
  liveLabel,
  orderDeployments,
} from "@/components/agent/lab/deployments";
import type { DeploymentRow } from "@/lib/api";
import {
  formatCompactUsd,
  formatCurrencyPnl,
  formatDateTime,
  pnlTextClass,
} from "@/lib/formatters";

/**
 * What this run put into the world (FEAT-100).
 *
 * The bots it deployed, the controllers those bots ran, and the standalone
 * executors it created — each with the tick that created it, when it started,
 * whether the run still holds it, and what it has made since.
 *
 * Before this the question could only be answered by leaving the agent: the
 * strategy page's one gesture was *View in fleet*, which folded the strategy's
 * **whole history** rather than the run you were reading, and nothing anywhere
 * mapped a deployment back to the tick that decided it. Each row here links into
 * the fleet at its own address instead.
 *
 * A run that deployed nothing says so. For a research or a consulting run that
 * is the true and useful answer, not an error and not an empty table frame.
 */
export function DeploymentLedger({ rows }: { rows: DeploymentRow[] }) {
  const ordered = useMemo(() => orderDeployments(rows), [rows]);

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        <Package className="h-3.5 w-3.5" /> Deployed
        {ordered.length > 0 && ` (${ordered.length})`}
      </h3>
      {ordered.length === 0 ? (
        <p className="text-[11px] text-[var(--color-text-muted)]">
          This run deployed nothing.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-[9px] uppercase tracking-widest text-[var(--color-text-muted)]">
                <th className="px-2 py-1.5 font-bold">What</th>
                <th className="px-2 py-1.5 font-bold">Created at</th>
                <th className="px-2 py-1.5 font-bold">Since</th>
                <th className="px-2 py-1.5 font-bold">Live</th>
                <th className="px-2 py-1.5 text-right font-bold">PnL</th>
                <th className="px-2 py-1.5 text-right font-bold">Volume</th>
                <th className="px-2 py-1.5" />
              </tr>
            </thead>
            <tbody>
              {ordered.map((row, i) => (
                <LedgerRow key={`${row.kind}-${row.label}-${i}`} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LedgerRow({ row }: { row: DeploymentRow }) {
  const href = fleetHref(row);
  return (
    <tr className="border-t border-[var(--color-border)]/25">
      <td className="px-2 py-1.5">
        <div className="flex items-center gap-1.5">
          <span aria-hidden="true">{kindIcon(row.kind)}</span>
          <span className="font-mono text-[var(--color-text)]">{row.label}</span>
        </div>
        {row.detail && (
          <div className="pl-5 text-[10px] text-[var(--color-text-muted)]">
            {row.detail}
          </div>
        )}
      </td>
      {/* The tick join is a heuristic and is labelled as one: a record with no
          creating call in range reads `—` rather than a fabricated number. */}
      <td className="px-2 py-1.5 text-[11px] text-[var(--color-text-muted)]">
        {formatTick(row.created_tick)}
      </td>
      <td className="px-2 py-1.5 font-mono text-[11px] text-[var(--color-text-muted)]">
        {row.started_at > 0 ? formatDateTime(row.started_at * 1000) : "—"}
        {row.ended_at != null && (
          <span className="text-[var(--color-text-muted)]">
            {" → "}
            {formatDateTime(row.ended_at * 1000)}
          </span>
        )}
      </td>
      <td className="px-2 py-1.5">
        {/* Ownership, never the performance snapshot's `status` — an archived
            instance still reports "running". */}
        <span
          className={`rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase ${
            row.live
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
              : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
          }`}
        >
          {liveLabel(row)}
        </span>
      </td>
      <td className={`px-2 py-1.5 text-right font-mono ${pnlTextClass(row.pnl)}`}>
        {formatCurrencyPnl(row.pnl)}
      </td>
      <td className="px-2 py-1.5 text-right font-mono text-[11px] text-[var(--color-text-muted)]">
        {formatCompactUsd(row.volume)}
      </td>
      <td className="px-2 py-1.5 text-right">
        {href && (
          <Link
            to={href}
            title={`See ${row.label} in the fleet`}
            className="inline-flex text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
          >
            <ExternalLink className="h-3 w-3" />
          </Link>
        )}
      </td>
    </tr>
  );
}
