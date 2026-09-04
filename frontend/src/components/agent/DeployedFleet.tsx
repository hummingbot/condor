import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Layers, Server } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import {
  BEFORE_LEDGER,
  BEFORE_LEDGER_LABEL,
} from "@/components/perf/agentFilter";
import { ControllerToggle } from "@/components/perf/ControllerToggle";
import { useFleetData } from "@/hooks/useFleetData";
import { attributionOf } from "@/lib/agent-attribution";
import { api, type ControllerInfo } from "@/lib/api";

/**
 * What this strategy put into the world, on the strategy's own page.
 *
 * The workbench could already tell you everything about the *loop* — its
 * cadence, its ticks, what the last one decided — and nothing whatsoever about
 * the **controllers it deployed**, which is the only part of it that is
 * spending money. The one route to those was a button that navigated to
 * `/bots?scope=agent:…`: a different page, the whole fleet re-rooted, the
 * strategy you were reading gone from the screen. "Show me what this agent
 * runs" should not cost the thing you were looking at.
 *
 * So the records come here instead, narrowed to this run key and to nothing
 * else. That narrowing is the feature: a reader on a strategy is asking about
 * *their* scope, and every other controller on the server is noise.
 *
 * ## Where the rows come from
 *
 * `useFleetData` under the same query keys `/bots` and the workspace's Fleet
 * view use, so this host adds no fetching of its own — react-query hands all
 * three the one set of records. Ownership is decided by `attributionOf`, the
 * same rule `AgentFleet`'s counts and `PerfBrowser`'s tree apply, so the three
 * surfaces can never disagree about which controllers are this strategy's.
 *
 * ## Why "nothing yet" is three different sentences
 *
 * An empty list has three causes and they want different actions, so the empty
 * state says which one it is rather than printing "No controllers" over all of
 * them:
 *
 * * the loop genuinely deployed nothing;
 * * it deployed onto a server the app is not pointed at (it says which, and
 *   offers to move); or
 * * it deployed and Condor could not record the claim — the case every run
 *   before the ACP arguments fix is in, where `owned_bots.json` was never
 *   written and the fleet trades on unattributed. Saying "nothing deployed"
 *   there is the lie that sent a reader hunting a frontend bug for an hour.
 */
export function DeployedFleet({
  slug,
  sslug,
  serverName,
  dense = false,
}: {
  slug: string;
  sslug: string;
  /** The strategy's configured server; the ambient one when it pins none. */
  serverName: string;
  /** Half a workspace row rather than a page: fewer columns per row. */
  dense?: boolean;
}) {
  const runKey = `${slug}.${sslug}`;

  const fleet = useFleetData(serverName || null, { population: "running" });

  /** The controllers this run key owns, and the totals across them. */
  const { mine, total } = useMemo(() => {
    const owned: ControllerInfo[] = [];
    for (const ctrl of fleet.controllers) {
      const who = attributionOf(
        fleet.owners,
        fleet.deeds,
        ctrl.bot_name,
        ctrl.controller_id || ctrl.controller_name,
      );
      if (who.runKey === runKey) owned.push(ctrl);
    }
    return { mine: owned, total: fleet.controllers.length };
  }, [fleet.controllers, fleet.owners, fleet.deeds, runKey]);

  /**
   * Bots on this server that no run key owns, with the moment they went up.
   *
   * The candidates for a claim, and the reason the claim is worth anything: the
   * ledger slices PnL over the window it owns a bot for, so the deploy time is
   * what turns "this strategy owns it from now" into "this strategy made what
   * it has already made". The earliest `deployed_at` across a bot's controllers
   * is when the bot itself went up.
   */
  const unowned = useMemo(() => {
    const earliest = new Map<string, number>();
    for (const ctrl of fleet.controllers) {
      const who = attributionOf(
        fleet.owners,
        fleet.deeds,
        ctrl.bot_name,
        ctrl.controller_id || ctrl.controller_name,
      );
      if (who.runKey || !ctrl.bot_name) continue;
      const at = ctrl.deployed_at ? Date.parse(ctrl.deployed_at) / 1000 : 0;
      const seen = earliest.get(ctrl.bot_name);
      // 0 means "unknown", and an unknown must never win a `Math.min` against a
      // real timestamp — that would claim the bot from 1970 and slice its whole
      // history in, including trading that predates this strategy.
      if (seen === undefined || (at > 0 && (seen === 0 || at < seen))) {
        earliest.set(ctrl.bot_name, at);
      }
    }
    return [...earliest.entries()]
      .map(([name, deployedAt]) => ({ name, deployedAt }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [fleet.controllers, fleet.owners, fleet.deeds]);

  /** One row per bot, because a deployed fleet is a bot with N controllers. */
  const byBot = useMemo(() => {
    const groups = new Map<string, ControllerInfo[]>();
    for (const ctrl of mine) {
      const list = groups.get(ctrl.bot_name);
      if (list) list.push(ctrl);
      else groups.set(ctrl.bot_name, [ctrl]);
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [mine]);

  /**
   * The money, folded into the display currency.
   *
   * `convert` returns `{ value, converted }` and the second half is not
   * decoration: a quote with no rate path comes back in its **own** units, so
   * adding it in silently would report a BRL figure as dollars — overstating a
   * BRL fleet by the whole BRL/USD rate. It is folded at face value, the way
   * `PerfBrowser`'s `UnpricedNote` does, and then said out loud.
   */
  const totals = useMemo(
    () =>
      mine.reduce(
        (acc, c) => {
          const pnl = fleet.convert(c.global_pnl_quote, quoteOf(c));
          const volume = fleet.convert(c.volume_traded, quoteOf(c));
          return {
            pnl: acc.pnl + pnl.value,
            volume: acc.volume + volume.value,
            unconverted: acc.unconverted + (pnl.converted ? 0 : 1),
          };
        },
        { pnl: 0, volume: 0, unconverted: 0 },
      ),
    [mine, fleet],
  );

  const scope = `/bots?scope=${encodeURIComponent(`agent:${runKey}`)}`;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Layers className="h-3.5 w-3.5" /> Deployed
          {mine.length > 0 && (
            <span className="font-mono tabular-nums text-[var(--color-text)]">
              {mine.length}
            </span>
          )}
        </h3>
        <Link
          to={scope}
          className="flex items-center gap-0.5 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
          title="The same records, beside everything else that is trading"
        >
          In the fleet
          <ChevronRight className="h-3 w-3" />
        </Link>
      </div>

      {mine.length > 0 && (
        <div className="mb-3 flex flex-wrap items-baseline gap-x-5 gap-y-1 border-b border-[var(--color-border)]/50 pb-3">
          <Stat
            label="PnL"
            value={`${totals.pnl >= 0 ? "+" : ""}${fleet.currencySymbol}${fmt(totals.pnl)}`}
            tone={totals.pnl >= 0 ? "text-emerald-500" : "text-[var(--color-red)]"}
          />
          <Stat
            label="Volume"
            value={`${fleet.currencySymbol}${fmt(totals.volume)}`}
          />
          <Stat
            label="Controllers"
            value={`${mine.length} of ${total} on ${serverName || "this server"}`}
          />
          {totals.unconverted > 0 && (
            <span
              className="w-full text-[10px] text-amber-500/90"
              title="No rate path from these controllers' quote currency to the display currency."
            >
              {totals.unconverted} of these could not be converted — counted at
              face value, not in {fleet.currencySymbol}
            </span>
          )}
        </div>
      )}

      {mine.length === 0 ? (
        <EmptyReason
          loading={fleet.isLoading}
          slug={slug}
          sslug={sslug}
          serverName={serverName}
          total={total}
          unowned={unowned}
        />
      ) : (
        <div className="space-y-3">
          {byBot.map(([botName, controllers]) => (
            <div key={botName}>
              <Link
                to={scope}
                className="mb-1 flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
              >
                <Server className="h-3 w-3 shrink-0" />
                <span className="truncate">{botName}</span>
                <span className="opacity-60">· {controllers.length}</span>
              </Link>
              <div className="space-y-0.5">
                {controllers.map((ctrl) => (
                  <ControllerRow
                    key={ctrl.controller_id || ctrl.controller_name}
                    controller={ctrl}
                    server={serverName}
                    dense={dense}
                    formatPnl={fleet.rateFormatPnl}
                    formatValue={fleet.rateFormatValue}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The quote leg a controller's money is denominated in. */
function quoteOf(ctrl: ControllerInfo): string {
  return ctrl.trading_pair?.split("-")[1] || "USDT";
}

/** Two significant decimals, grouped — the same shape every money read here has. */
function fmt(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function Stat({
  label,
  value,
  tone = "text-[var(--color-text)]",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </span>
      <span className={`font-mono text-sm tabular-nums ${tone}`}>{value}</span>
    </span>
  );
}

/**
 * One controller: what it trades, whether it is alive, and what it has made.
 *
 * The controller *class* is not shown. It is the least distinguishing field on
 * a row — a fleet is six of the same class on six pairs — and the pair is the
 * one a reader scans for.
 */
function ControllerRow({
  controller,
  server,
  dense,
  formatPnl,
  formatValue,
}: {
  controller: ControllerInfo;
  /** Where it runs — the strategy's server, which is what the fleet was read from. */
  server: string;
  dense: boolean;
  /** The fleet's own formatters: they mark an unconverted figure with a ⚠. */
  formatPnl: (value: number, quote: string) => string;
  formatValue: (value: number, quote: string) => string;
}) {
  const quote = quoteOf(controller);
  const raw = controller.global_pnl_quote;
  // `status` on a controller is a hardcoded "running" upstream; what actually
  // says whether it is quoting is `manual_kill_switch` in its config. Reading
  // the switch keeps this row honest where the status field is not.
  const stopped = controller.config?.manual_kill_switch === true;

  return (
    <div className="flex items-center gap-2 rounded px-1 py-1 text-xs hover:bg-[var(--color-surface-hover)]">
      <span
        title={stopped ? "Stopped (kill switch on)" : "Running"}
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          stopped ? "bg-[var(--color-text-muted)]/40" : "bg-emerald-400"
        }`}
      />
      <span className="shrink-0 font-medium text-[var(--color-text)]">
        {controller.trading_pair || "—"}
      </span>
      {!dense && (
        <span className="truncate text-[11px] text-[var(--color-text-muted)]">
          {controller.connector}
        </span>
      )}
      <span
        className={`ml-auto shrink-0 font-mono tabular-nums ${
          raw >= 0 ? "text-emerald-500" : "text-[var(--color-red)]"
        }`}
      >
        {raw >= 0 ? "+" : ""}
        {formatPnl(raw, quote)}
      </span>
      <span className="w-24 shrink-0 truncate text-right font-mono text-[10px] tabular-nums text-[var(--color-text-muted)]">
        {formatValue(controller.volume_traded, quote)}
      </span>
      {/* The same control the execution dock and the fleet browser carry, so a
          reader who has found their strategy's controllers here does not have
          to go and find them again somewhere else to pause one. Drawn only when
          the server is known: without one there is nothing to post to. */}
      {server && (
        <ControllerToggle
          server={server}
          bot={controller.bot_name}
          controllerId={controller.controller_id || controller.controller_name}
          stopped={stopped}
          label={controller.controller_id || controller.controller_name}
        />
      )}
    </div>
  );
}

/**
 * Why there is nothing here — which is a different question from whether there
 * is anything here.
 *
 * The unattributed count is the load-bearing one. A server carrying
 * controllers that belong to *nobody* while this strategy has run is the
 * signature of a lost ownership claim, and the reader needs to be told that
 * rather than left to conclude their agent did nothing.
 *
 * And told with the repair in reach: each unowned bot gets a **Claim** button,
 * which writes it into the strategy's ledger from the moment it was deployed,
 * so the back-fill reports what the run actually made rather than starting the
 * clock at the click. Manual on purpose — a bot outside the namespace looks
 * exactly like somebody else's bot, and a sweep that guessed would quietly move
 * money between two agents' books.
 */
function EmptyReason({
  loading,
  slug,
  sslug,
  serverName,
  total,
  unowned,
}: {
  loading: boolean;
  slug: string;
  sslug: string;
  serverName: string;
  total: number;
  /** Bots on this server that no run key owns: `{ name, deployedAt }`. */
  unowned: readonly { name: string; deployedAt: number }[];
}) {
  const queryClient = useQueryClient();
  const claim = useMutation({
    mutationFn: (bot: { name: string; deployedAt: number }) =>
      api.claimBot(slug, sslug, bot.name, bot.deployedAt),
    onSuccess: () => {
      // The ledger decides the fleet map, the strategy's money and the tree.
      queryClient.invalidateQueries({ queryKey: ["fleet-map"] });
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
    },
  });

  if (loading) {
    return (
      <p className="text-xs text-[var(--color-text-muted)]">Reading the fleet…</p>
    );
  }

  if (!serverName) {
    return (
      <p className="text-xs text-[var(--color-text-muted)]">
        This strategy has no server pinned and none is selected, so there is no
        fleet to read.
      </p>
    );
  }

  return (
    <div className="space-y-2 text-xs text-[var(--color-text-muted)]">
      <p>
        Nothing on <code className="font-mono">{serverName}</code> is attributed
        to this strategy.
      </p>
      {total > 0 && (
        <p>
          {total} controller{total === 1 ? " is" : "s are"} running there under
          other owners — if one of them is this strategy&apos;s, its ownership
          claim never landed, and it is under{" "}
          <Link
            to={`/bots?scope=${encodeURIComponent(`agent:${BEFORE_LEDGER}`)}`}
            className="underline underline-offset-2 transition-colors hover:text-[var(--color-primary)]"
          >
            {BEFORE_LEDGER_LABEL}
          </Link>
          .
        </p>
      )}
      {unowned.length > 0 && (
        <div className="space-y-1 rounded border border-[var(--color-border)] p-2">
          <p className="text-[11px]">
            Unclaimed bots on this server. Claim one and this strategy owns it
            from when it was deployed:
          </p>
          {unowned.map((bot) => (
            <div key={bot.name} className="flex items-center gap-2">
              <span className="truncate font-mono text-[11px] text-[var(--color-text)]">
                {bot.name}
              </span>
              <button
                type="button"
                disabled={claim.isPending}
                onClick={() => claim.mutate(bot)}
                className="ml-auto shrink-0 rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] font-semibold transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)] disabled:opacity-40"
              >
                {claim.isPending ? "Claiming…" : "Claim"}
              </button>
            </div>
          ))}
          {claim.isError && (
            <p className="text-[11px] text-[var(--color-red)]">
              {claim.error instanceof Error
                ? claim.error.message
                : "Could not claim it."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
