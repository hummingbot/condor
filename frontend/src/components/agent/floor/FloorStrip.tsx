import { feeBps, turnover, type FloorModel } from "@/components/agent/floor/floor";
import {
  formatCurrencyPnl,
  formatCurrencyVolume,
  formatRuntimeHours,
  pnlTextClass,
} from "@/lib/formatters";

/**
 * What the whole fleet adds up to, in one line across the top (FEAT-112).
 *
 * The first six figures are `Σ` over the rows and the named non-agent parts —
 * the same fold, read once. The last two are the readings that survive a change
 * of account size, and they are the reason this is a strip rather than a
 * headline: a fleet that doubles its capital doubles its PnL and its volume and
 * changes neither its cost per unit traded nor how hard it works what it has.
 *
 * Two rules run through it, and both are refusals:
 *
 *  - **Fees carry the executor-only caption.** `leafFromController` hardcodes
 *    `fees: 0` because *"the controllers payload reports no fee total of its
 *    own"*, so a fleet fee total is a floor, not a total, and a bps reading
 *    without that caption is a lie with a decimal point on it.
 *  - **A reading with a zero denominator is suppressed, not zeroed.** `0.0 bps`
 *    and `0.0×` are statements about a fleet that traded; a fleet that has not
 *    traded has made no statement, and the two look identical on a screen that
 *    prints the number anyway (`attributedMoney`'s rule).
 *
 * And what it does **not** say is said out loud beneath it: margin, leverage and
 * account health, live orders and fill-level flow, and sub-accounts have no
 * source anywhere in this app's records. Captioned as not measured rather than
 * drawn as an empty panel, which reads as a number that has not loaded yet.
 */
export function FloorStrip({
  model,
  servers,
}: {
  model: FloorModel;
  /** How many servers answered — the honest scope of every figure here. */
  servers: number;
}) {
  const t = model.total;
  const symbol = model.symbol;
  const bps = feeBps(t);
  const turns = turnover(t);
  const agents = model.rows.length;

  return (
    <header
      data-floor-strip
      className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2"
    >
      <div className="mx-auto w-full max-w-6xl">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-sm font-semibold tracking-tight">Floor</h1>
          <p className="text-[11px] text-[var(--color-text-muted)]">
            {agents === 0
              ? "No agent's records have been folded yet."
              : `${agents} agent${agents === 1 ? "" : "s"} over ${servers} server${
                  servers === 1 ? "" : "s"
                } · ${t.count} record${t.count === 1 ? "" : "s"}`}
          </p>
        </div>

        <dl className="mt-1.5 flex flex-wrap items-start gap-x-6 gap-y-2">
          <Stat label="Net">
            <span
              data-floor-net
              className={`font-mono text-sm font-semibold ${pnlTextClass(t.net)}`}
            >
              {formatCurrencyPnl(t.net, symbol)}
            </span>
            <span className="block text-[10px] text-[var(--color-text-muted)]">
              {formatCurrencyPnl(t.realized, symbol)} real ·{" "}
              {formatCurrencyPnl(t.unrealized, symbol)} unreal
            </span>
          </Stat>

          <Stat label="Volume">
            <Value data-floor-volume>{formatCurrencyVolume(t.volume, symbol)}</Value>
          </Stat>

          <Stat label="Open">
            <Value>{t.positions}</Value>
            <Caption>position{t.positions === 1 ? "" : "s"}</Caption>
          </Stat>

          <Stat label="Capital">
            <Value>
              {t.capital > 0 ? formatCurrencyVolume(t.capital, symbol) : "—"}
            </Value>
            <Caption>declared</Caption>
          </Stat>

          <Stat label="Win rate">
            <Value>
              {t.winRate === undefined ? "—" : `${(t.winRate * 100).toFixed(0)}%`}
            </Value>
            <Caption>
              {t.closed > 0 ? `${t.wins}/${t.closed} closed` : "nothing closed"}
            </Caption>
          </Stat>

          <Stat label="Runtime">
            <Value>{t.hours > 0 ? formatRuntimeHours(t.hours) : "—"}</Value>
            <Caption>measured</Caption>
          </Stat>

          {/* The two normalized readings. Suppressed rather than zeroed. */}
          <Stat label="Fees">
            <Value data-floor-bps>{bps === null ? "—" : `${bps.toFixed(1)} bps`}</Value>
            <Caption title="Controllers report no fee total of their own, so this counts executor fees over the whole fleet's volume — a floor, not a total.">
              executor-only, of volume
            </Caption>
          </Stat>

          <Stat label="Turnover">
            <Value data-floor-turnover>
              {turns === null ? "—" : `${turns.toFixed(1)}×`}
            </Value>
            <Caption>volume / declared capital</Caption>
          </Stat>
        </dl>

        {/* The residual of the residuals. Zero by construction — the agent
            spines partition the root spine — so it is shown only if it is not,
            which would mean the tree's rule changed under this page. */}
        {Math.abs(model.unaccounted) > 0.005 && (
          <p
            data-floor-unaccounted
            className="mt-1 text-[11px] text-[var(--color-yellow)]"
          >
            {formatCurrencyPnl(model.unaccounted, symbol)} is in the fleet fold and
            in none of the parts below.
          </p>
        )}

        <p
          data-floor-not-measured
          className="mt-1 text-[10px] leading-relaxed text-[var(--color-text-muted)]"
        >
          Not measured here: margin, leverage and account health; live orders and
          fill-level flow; sub-accounts. Condor's records carry none of them —
          there is no accounts or orders route, and no record names an account —
          so nothing on this page stands in for them.
        </p>
      </div>
    </header>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[9px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        {label}
      </dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function Value({
  children,
  ...rest
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className="block font-mono text-sm font-semibold" {...rest}>
      {children}
    </span>
  );
}

function Caption({
  children,
  title,
}: {
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`block text-[10px] text-[var(--color-text-muted)] ${
        title ? "cursor-help" : ""
      }`}
    >
      {children}
    </span>
  );
}
