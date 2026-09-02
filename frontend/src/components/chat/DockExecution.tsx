import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { useRates } from "@/hooks/useRates";
import { api, type ControllerInfo } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { formatCurrencyPnl, isExecutorActive, pnlColor } from "@/lib/formatters";
import {
  UNATTACHED_BOT,
  controllerNodeId,
  leafFromController,
  type PerfLeaf,
} from "@/lib/perf-tree";
import { executorsQuery } from "@/lib/queryClient";

/** How many controllers fit before the panel stops being a glance. */
const MAX_ROWS = 6;

/** The quote a controller's PnL is denominated in — its pair's, as elsewhere. */
function quoteOf(pair: string): string {
  return pair.split("-")[1] || "USDT";
}

/**
 * What is trading right now on the server this conversation is about.
 *
 * The live fleet, in the shape the perf browser folds it: controllers grouped
 * under the bot that deployed them, the executors each is running, and the
 * executors no live controller claims. Every row deep-links into `/bots` by the
 * `?scope=` id the browser reads (FEAT-084, FEAT-086) — and builds that id by
 * calling `controllerNodeId` on the same `PerfLeaf` the browser folds, so the
 * two can only drift if the browser's own tree does.
 *
 * **Running is the kill switch, not `status`.** The `/bots` payload hardcodes
 * `"running"` for every controller it reports; what actually stops one is
 * `config.manual_kill_switch`. `leafFromController` is where that is resolved,
 * so nothing in this file reads `c.status` — on the one panel whose whole job
 * is answering "what is running", a paused controller listed as live would be
 * the exact wrong answer.
 *
 * Read-only by design. Stopping a controller belongs to the scope that owns it
 * in the browser, where a confirmation has room to say what it is stopping.
 *
 * Mounted only while the section is open (see `DockSection`): closed, neither
 * query nor the socket subscription below exists.
 */
export function DockExecution({ server }: { server: string }) {
  const navigate = useNavigate();

  // Live frames, with a relaxed poll behind them — the arrangement `/portfolio`
  // already uses. The socket is shared and ref-counted per channel, so a second
  // subscriber to a channel a page already holds costs nothing on the wire.
  const channels = useMemo(() => ["bots", `executors:${server}`], [server]);
  useCondorWebSocket(channels, server);

  const { data: bots, isLoading, error } = useQuery({
    queryKey: ["bots", server],
    queryFn: () => api.getBots(server),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });

  const { data: executors } = useQuery({
    queryKey: executorsQuery(server).queryKey,
    queryFn: () => api.getExecutors(server),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });

  /**
   * The controllers actually trading, deduped the way everything that keys a
   * controller has to be: on bot + config id, because one config deployed to
   * two bots is two independent controllers sharing an id (CORR-241).
   */
  const live = useMemo<PerfLeaf[]>(() => {
    const seen = new Map<string, ControllerInfo>();
    for (const c of bots?.controllers ?? []) seen.set(controllerKey(c), c);
    return [...seen.values()]
      .map(leafFromController)
      .filter((leaf) => leaf.status !== "stopped")
      .sort((a, b) => a.bot.localeCompare(b.bot) || a.label.localeCompare(b.label));
  }, [bots]);

  /**
   * How many live executors each controller is running, and how many nobody
   * claims.
   *
   * The record carries a `controller_id` and no bot, so the bot is looked up
   * from the live fleet — the same rule `leafFromExecutor` documents, filing an
   * executor that matches no live controller under `(unattached)`.
   */
  const { byController, unattached, liveExecutors } = useMemo(() => {
    const botByController = new Map<string, string>();
    for (const leaf of live) botByController.set(leaf.controllerId, leaf.bot);

    const counts = new Map<string, number>();
    let orphans = 0;
    let total = 0;
    for (const ex of executors ?? []) {
      if (!isExecutorActive(ex.status)) continue;
      total += 1;
      const bot = botByController.get(ex.controller_id);
      if (!bot) {
        orphans += 1;
        continue;
      }
      const key = controllerKey({ bot_name: bot, controller_id: ex.controller_id });
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return { byController: counts, unattached: orphans, liveExecutors: total };
  }, [live, executors]);

  /**
   * The rows, capped and then grouped under the bot that deployed them.
   *
   * Grouped rather than flat because a bot name is the long half of a
   * controller's identity — real ones run to sixty characters — and repeating
   * it on every row in a 320px column truncates away the config id, which is
   * the only half that tells two rows apart. Capped *before* grouping so the
   * limit counts controllers, which is what the reader is scanning.
   */
  const groups = useMemo(() => {
    const out: { bot: string; leaves: PerfLeaf[] }[] = [];
    for (const leaf of live.slice(0, MAX_ROWS)) {
      const last = out[out.length - 1];
      if (last && last.bot === leaf.bot) last.leaves.push(leaf);
      else out.push({ bot: leaf.bot, leaves: [leaf] });
    }
    return out;
  }, [live]);

  const { convert, currencySymbol } = useRates(
    useMemo(() => live.map((leaf) => quoteOf(leaf.pair)), [live]),
  );

  const footer = (
    <button
      type="button"
      onClick={() => navigate("/bots")}
      className="flex w-full items-center gap-1 px-3 py-1.5 text-left text-[11px] text-[var(--color-primary)] transition-colors hover:bg-[var(--color-surface-hover)]"
    >
      Open execution
      <ArrowRight className="h-3 w-3" />
    </button>
  );

  if (error) {
    return (
      <div className="flex flex-col">
        <p className="px-3 py-2 text-[11px] text-[var(--color-red)]">
          Could not read {server}&apos;s fleet.
        </p>
        {footer}
      </div>
    );
  }

  if (isLoading && !bots) {
    return (
      <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
        Reading {server}…
      </p>
    );
  }

  if (!live.length && !unattached) {
    return (
      <div className="flex flex-col">
        <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
          No controllers running on {server}.
        </p>
        {footer}
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {/* What the two figures are counting, kept on screen while the list
          scrolls — the count is the answer, the rows are the detail. */}
      <div
        className="sticky top-0 z-10 flex items-baseline gap-2 bg-[var(--color-bg)] px-3 pb-1 pt-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]"
        data-testid="execution-counts"
      >
        <span className="min-w-0 flex-1 truncate">
          {live.length} controller{live.length === 1 ? "" : "s"}
        </span>
        <span className="shrink-0 tabular-nums">
          {liveExecutors} executor{liveExecutors === 1 ? "" : "s"}
        </span>
      </div>

      <ul className="px-1">
        {groups.map((group) => (
          <li key={group.bot}>
            {/* The bot, once per group: its whole branch in the browser, and
                the level the executors under it are counted at. */}
            <button
              type="button"
              onClick={() => navigate(`/bots?scope=bot:${group.bot}`)}
              title={`Everything ${group.bot} is running`}
              data-bot-group
              className="flex w-full items-baseline gap-2 rounded px-2 pt-1 text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            >
              <span className="min-w-0 flex-1 truncate">{group.bot}</span>
            </button>
            <ul>
              {group.leaves.map((leaf) => {
                const count = byController.get(leaf.id) ?? 0;
                const net = convert(leaf.net, quoteOf(leaf.pair)).value;
                return (
                  <li key={leaf.id} className="pb-0.5">
                    <button
                      type="button"
                      onClick={() => navigate(`/bots?scope=${controllerNodeId(leaf)}`)}
                      title={`${leaf.label} on ${leaf.bot}`}
                      data-controller-row
                      className="flex w-full flex-col gap-0 rounded px-2 py-0.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
                    >
                      <span className="flex w-full items-baseline gap-2 text-[11px]">
                        <span className="min-w-0 flex-1 truncate">
                          {leaf.label}
                        </span>
                        <span
                          className="shrink-0 font-mono tabular-nums"
                          style={{ color: pnlColor(net) }}
                        >
                          {formatCurrencyPnl(net, currencySymbol)}
                        </span>
                      </span>
                      <span className="flex w-full items-baseline gap-2 text-[10px] text-[var(--color-text-muted)]">
                        <span className="min-w-0 flex-1 truncate">
                          ↳ {count} executor{count === 1 ? "" : "s"}
                        </span>
                        <span className="shrink-0 truncate">{leaf.pair}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
        {live.length > MAX_ROWS && (
          <li className="px-2 py-0.5 text-[10px] text-[var(--color-text-muted)]">
            +{live.length - MAX_ROWS} more
          </li>
        )}
      </ul>

      {unattached > 0 && (
        <button
          type="button"
          onClick={() => navigate("/bots?population=running")}
          title="Live executors no running controller claims"
          className="flex w-full items-baseline gap-2 border-t border-[var(--color-border)] px-3 py-1 text-left text-[11px] transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          <span className="min-w-0 flex-1 truncate text-[var(--color-text-muted)]">
            {UNATTACHED_BOT}
          </span>
          <span className="shrink-0 tabular-nums text-[var(--color-text-muted)]">
            {unattached} executor{unattached === 1 ? "" : "s"}
          </span>
        </button>
      )}

      {footer}
    </div>
  );
}
