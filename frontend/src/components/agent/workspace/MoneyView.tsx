import { useQueries } from "@tanstack/react-query";
import { ArrowUpRight, Coins, HelpCircle } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { PerformancePanel } from "@/components/agent/AgentOverviewTab";
import {
  recordsHref,
  reconcile,
  type Lead,
  type Term,
} from "@/components/agent/workspace/reconcile";
import { useFleetData } from "@/hooks/useFleetData";
import { useSeconds } from "@/hooks/useSeconds";
import { useServer } from "@/hooks/useServer";
import { api, type StrategySummary } from "@/lib/api";
import {
  formatCurrencyPnl,
  formatCurrencyVolume,
  pnlTextClass,
} from "@/lib/formatters";
import { runningLeaves } from "@/lib/perf-population";

/**
 * What this agent has made — both answers, named apart (FEAT-109).
 *
 * This view used to be `PerformancePanel` alone, which is the **run rollup**:
 * per session, owner-window tiled, answering *"how much did session 3 make
 * while it owned this bot?"*. That is a real answer and the Lab, the run rail
 * and the deployment ledger are all built around it. It is simply not the same
 * quantity as the one `/bots` prints at `?scope=agent:{runKey}`, which is the
 * **fold**: every record the agent owns, as it stands right now.
 *
 * Both are correct. Shown as one number, one of them is a lie by omission — the
 * operator reads `+$64` here, `+$91` on the fleet page, and has no way to know
 * that neither is broken. So there are three bands:
 *
 * 1. **The headline** is the fold, because that is the number an operator acts
 *    on and because matching the fleet page by construction is what makes the
 *    two screens trustworthy together.
 * 2. **The reconciliation** accounts for the difference, and it is the band that
 *    makes the design honest. Every term is a named set of records the reader
 *    can open; anything that cannot be named stays in *unaccounted*, with a
 *    lead, rather than being filed under "other".
 * 3. **What its runs earned** is the rollup, unchanged, labelled as what it is.
 *
 * Every judgement is in `reconcile.ts`; this file is markup and three queries.
 *
 * **Which server.** The agent's own — the strategy's configured one, else the
 * agent's pin, else the ambient one — the same rule `AgentFleet` follows and
 * for the same reason: an agent owns its namespace wherever its bots run, and
 * reading the ambient server would report an empty fold for an agent that
 * trades somewhere else.
 */
export function MoneyView({
  slug,
  sslug,
  strategy,
  strategies,
  serverName,
}: {
  slug: string;
  /** The strategy the workspace resolved — the scope of the rollup band. */
  sslug: string;
  /** `?strategy=` when the URL names one, else `null`. Narrows the fold. */
  strategy: string | null;
  /** Every strategy the agent owns, for the agent-wide rollup. */
  strategies: readonly StrategySummary[];
  /** The agent's own server: the strategy's config, else the agent's pin. */
  serverName: string;
}) {
  const { server: ambient } = useServer();
  const server = serverName || ambient;
  const fleet = useFleetData(server, { population: "running" });

  // The fold's clock feeds only the measured runtime, which this view does not
  // print — so a mount-time reading is enough, and it keeps the render pure.
  const now = useSeconds(false);

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

  /**
   * The run rollup, over every strategy in scope.
   *
   * One query per strategy, under `PerformancePanel`'s own key — so the band at
   * the bottom of this page is served from this cache rather than fetching the
   * same answer a second time, and no new endpoint is added for a number that
   * is already computed.
   */
  const scoped = useMemo(
    () => (strategy ? strategies.filter((s) => s.slug === strategy) : strategies),
    [strategies, strategy],
  );
  const rollups = useQueries({
    queries: scoped.map((s) => ({
      queryKey: ["strategy-performance", slug, s.slug],
      queryFn: () => api.getStrategyPerformance(slug, s.slug),
      refetchInterval: 10000,
    })),
  });

  /**
   * `null` until every strategy in scope has answered.
   *
   * A partial sum would be a smaller number that looks like a finished one, and
   * the whole reconciliation would then blame the difference on the fleet.
   */
  const attributed = rollups.every((q) => q.data)
    ? rollups.reduce((sum, q) => sum + Number(q.data?.totals?.total_pnl ?? 0), 0)
    : null;

  const r = useMemo(
    () =>
      reconcile({
        slug,
        strategy,
        leaves,
        deeds: fleet.deeds,
        convert: fleet.convert,
        now,
        attributed,
      }),
    [slug, strategy, leaves, fleet.deeds, fleet.convert, now, attributed],
  );

  const symbol = fleet.currencySymbol;
  const href = (item: Term | Lead) => recordsHref(slug, sslug, item);

  return (
    <div className="space-y-4">
      {/* ── The headline: the fold, in the fleet page's own formatters ── */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Coins className="h-3.5 w-3.5" /> What its records show
        </h3>
        <div className="flex flex-wrap items-baseline gap-x-8 gap-y-2">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              Net
            </span>
            {/* A fold with nothing in it has not made `$0.00`, it has made no
                statement — FEAT-104's rule, which holds on every agent surface. */}
            <span
              data-money-net
              className={`font-mono text-2xl font-semibold ${
                r.reported ? pnlTextClass(r.fold) : "text-[var(--color-text-muted)]"
              }`}
            >
              {r.reported ? formatCurrencyPnl(r.fold, symbol) : "—"}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              Volume
            </span>
            <span data-money-volume className="font-mono text-2xl text-[var(--color-text)]">
              {r.reported ? formatCurrencyVolume(r.totals.volume, symbol) : "—"}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              Open
            </span>
            <span className="font-mono text-2xl text-[var(--color-text)]">
              {r.totals.positions}
            </span>
          </div>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          Every record this agent owns, folded as it stands now — the same
          computation the fleet page makes at{" "}
          <code className="font-mono">?scope=agent:</code>, over the same records.
          {r.runKeys.length > 0 && (
            <>
              {" "}
              <Link
                to={`/agents/${encodeURIComponent(slug)}?view=fleet&strategy=${encodeURIComponent(sslug)}`}
                className="inline-flex items-center gap-0.5 underline-offset-2 hover:text-[var(--color-primary)] hover:underline"
              >
                Open the fleet <ArrowUpRight className="h-3 w-3" />
              </Link>
            </>
          )}
        </p>
      </div>

      {/* ── The reconciliation: the band that makes the two numbers honest ── */}
      <div
        data-money-reconciliation
        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
      >
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <HelpCircle className="h-3.5 w-3.5" /> Why the two numbers differ
        </h3>

        {r.attributed === null ? (
          <p className="text-xs text-[var(--color-text-muted)]">
            Waiting on what its runs earned — until that arrives there is only
            one number, and nothing to reconcile.
          </p>
        ) : (
          <ul className="space-y-1.5 text-xs">
            <Line
              label="What its runs earned"
              value={formatCurrencyPnl(r.attributed, symbol)}
              muted
            />
            {r.terms.map((term) => (
              <Line
                key={term.scope}
                data-money-term={term.scope}
                label={term.label}
                sub={`${term.count} record${term.count === 1 ? "" : "s"}`}
                value={formatCurrencyPnl(term.delta, symbol)}
                to={href(term)}
              />
            ))}
            {/* Never folded into a term: a residual that cannot be named is a
                finding, not a rounding, and it is shown as one. */}
            {r.unaccounted !== 0 && (
              <Line
                data-money-unaccounted
                label="Unaccounted"
                sub={
                  r.leads.length > 0
                    ? "the records below are the likeliest cause"
                    : "no named set of records explains this"
                }
                value={formatCurrencyPnl(r.unaccounted, symbol)}
                warn
              />
            )}
            <Line
              label="What its records show"
              value={r.reported ? formatCurrencyPnl(r.fold, symbol) : "—"}
              strong
            />
          </ul>
        )}

        {r.unaccounted !== 0 && r.leads.length > 0 && (
          <ul className="mt-3 space-y-1 border-t border-[var(--color-border)] pt-2 text-[11px]">
            {r.leads.map((lead) => (
              <li key={lead.scope}>
                <Link
                  data-money-lead={lead.scope}
                  to={href(lead)}
                  className="inline-flex items-center gap-1 text-[var(--color-text-muted)] underline-offset-2 transition-colors hover:text-[var(--color-primary)] hover:underline"
                >
                  {lead.label} <ArrowUpRight className="h-3 w-3 shrink-0" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── The rollup, unchanged, under the heading that says what it is ── */}
      <div>
        <h3 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          What its runs earned
        </h3>
        <p className="mb-2 text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          Per session, sliced to the windows each run actually owned its bots —
          a historical question about runs, not a present-tense one about
          records. It is not the same quantity as the headline above.
        </p>
        <PerformancePanel slug={slug} sslug={sslug} dense />
      </div>
    </div>
  );
}

/** One row of the reconciliation: a name, what it is worth, and where it is. */
function Line({
  label,
  sub,
  value,
  to,
  muted,
  strong,
  warn,
  ...rest
}: {
  label: string;
  sub?: string;
  value: string;
  to?: string;
  muted?: boolean;
  strong?: boolean;
  warn?: boolean;
} & Record<`data-${string}`, string | undefined>) {
  const name = (
    <span className="flex items-baseline gap-1.5">
      <span>{label}</span>
      {sub && (
        <span className="text-[10px] text-[var(--color-text-muted)]">{sub}</span>
      )}
      {to && <ArrowUpRight className="h-3 w-3 shrink-0 opacity-50" />}
    </span>
  );

  return (
    <li
      {...rest}
      className={`flex items-baseline justify-between gap-4 ${
        strong ? "border-t border-[var(--color-border)] pt-1.5 font-semibold" : ""
      } ${warn ? "text-[var(--color-yellow)]" : muted ? "text-[var(--color-text-muted)]" : ""}`}
    >
      {to ? (
        <Link
          to={to}
          className="min-w-0 underline-offset-2 transition-colors hover:text-[var(--color-primary)] hover:underline"
        >
          {name}
        </Link>
      ) : (
        name
      )}
      <span className="shrink-0 font-mono tabular-nums">{value}</span>
    </li>
  );
}
