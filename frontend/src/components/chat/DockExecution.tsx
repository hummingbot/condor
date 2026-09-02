import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Fragment, useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { useRates } from "@/hooks/useRates";
import { api, type ControllerInfo } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import {
  formatCompactVolume,
  formatCurrencyPnl,
  formatRuntimeHours,
  isExecutorActive,
  pnlColor,
} from "@/lib/formatters";
import {
  UNATTACHED_BOT,
  controllerNodeId,
  leafFromController,
  type PerfLeaf,
} from "@/lib/perf-tree";
import { executorsQuery } from "@/lib/queryClient";

/**
 * The columns, left to right — the header row and every body row read from it.
 *
 * One list rather than two parallel ones, because a table whose `<th>`s and
 * `<td>`s are written out separately drifts the moment a column is inserted in
 * the middle, and the failure is silent: every number is still rendered, one
 * heading to the left of what it means.
 *
 * `num` is what is right-aligned and tabular; the label column is not. `hint`
 * is the `title`, because a five-character heading has to be able to say what
 * it is somewhere.
 */
const COLUMNS: {
  key: string;
  label: string;
  hint: string;
  num: boolean;
  /** Fixed px, or `undefined` for the one column that absorbs the slack. */
  width?: number;
}[] = [
  { key: "controller", label: "Controller", hint: "Config id — click for its scope in /bots", num: false },
  { key: "pair", label: "Pair", hint: "The market it trades", num: false, width: 76 },
  { key: "exec", label: "Exec", hint: "Live executors it is running now", num: true, width: 38 },
  { key: "up", label: "Up", hint: "Time since it was deployed", num: true, width: 46 },
  { key: "vol", label: "Vol", hint: "Volume traded", num: true, width: 58 },
  { key: "realized", label: "Real.", hint: "Realized PnL", num: true, width: 68 },
  { key: "unrealized", label: "Unreal.", hint: "Unrealized PnL on open positions", num: true, width: 68 },
  { key: "net", label: "Net", hint: "The controller's own total — not realized + unrealized", num: true, width: 70 },
];

/** The width every column but the first is worth, plus a floor for the first. */
const FIXED_PX = COLUMNS.reduce((sum, c) => sum + (c.width ?? 0), 0);
/** Below this the controller column stops being readable and the table scrolls. */
const MIN_LABEL_PX = 120;
export const TABLE_MIN_PX = FIXED_PX + MIN_LABEL_PX;

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
   * The rows, grouped under the bot that deployed them.
   *
   * Grouped rather than flat because a bot name is the long half of a
   * controller's identity — real ones run to sixty characters — and repeating
   * it on every row truncates away the config id, which is the only half that
   * tells two rows apart. A group header spanning the table costs one line per
   * bot and gives every controller row its full width back.
   *
   * Uncapped. The list used to stop at six with a "+N more" line under it,
   * which was the right trade for a two-line-per-row list in a floating panel
   * and is the wrong one for a table in a column the reader sized themselves:
   * the whole point of a table is that row seven costs nothing to scan, and the
   * pane scrolls.
   */
  const groups = useMemo(() => {
    const out: { bot: string; leaves: PerfLeaf[] }[] = [];
    for (const leaf of live) {
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

      {/* A table, not a list of cards. Every controller answers the same eight
          questions, and eight answers per row only stay comparable when they
          are in eight columns: the two-line row this replaces put volume and
          PnL on different lines at different widths, so "which of these is
          losing money" was read by scrolling rather than by looking down a
          column. It fits whatever width the panel was dragged to and only
          scrolls sideways below `TABLE_MIN_PX`, where the controller name would
          otherwise be squeezed out of legibility. */}
      <div className="min-w-0 overflow-x-auto">
        <table
          className="w-full table-fixed border-collapse text-[11px]"
          style={{ minWidth: TABLE_MIN_PX }}
        >
          {/* Fixed layout, so the table is exactly as wide as the column it is
              in and the controller name absorbs whatever is left. Letting the
              browser size from content instead pushed Net off the right edge on
              a fleet whose bot names run to sixty characters — the widest cell
              won the negotiation and the most important number lost it. */}
          <colgroup>
            {COLUMNS.map((col) => (
              <col key={col.key} style={col.width ? { width: col.width } : undefined} />
            ))}
          </colgroup>
          <thead>
            <tr className="text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  scope="col"
                  title={col.hint}
                  className={`px-1.5 pb-1 font-medium ${
                    col.num ? "text-right" : "text-left"
                  } ${col.key === "controller" ? "pl-3" : ""} ${
                    col.key === "net" ? "pr-3" : ""
                  }`}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <Fragment key={group.bot}>
                {/* The bot, once per group: its whole branch in the browser,
                    and the level the executors under it are counted at. */}
                <tr>
                  <td colSpan={COLUMNS.length} className="p-0">
                    <button
                      type="button"
                      onClick={() => navigate(`/bots?scope=bot:${group.bot}`)}
                      title={`Everything ${group.bot} is running`}
                      data-bot-group
                      className="flex w-full items-baseline gap-2 px-3 pt-1.5 text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                    >
                      <span className="min-w-0 flex-1 truncate">{group.bot}</span>
                    </button>
                  </td>
                </tr>
                {group.leaves.map((leaf) => {
                  const quote = quoteOf(leaf.pair);
                  const money = (val: number) => convert(val, quote).value;
                  const net = money(leaf.net);
                  const realized = money(leaf.realized);
                  const unrealized = money(leaf.unrealized);
                  const uptime = leaf.startedAt
                    ? (Date.now() - leaf.startedAt) / 3_600_000
                    : NaN;
                  const scope = controllerNodeId(leaf);
                  return (
                    <tr
                      key={leaf.id}
                      data-controller-row
                      role="button"
                      tabIndex={0}
                      title={`${leaf.label} on ${leaf.bot}`}
                      onClick={() => navigate(`/bots?scope=${scope}`)}
                      onKeyDown={(e) => {
                        if (e.key !== "Enter" && e.key !== " ") return;
                        e.preventDefault();
                        navigate(`/bots?scope=${scope}`);
                      }}
                      className="cursor-pointer transition-colors hover:bg-[var(--color-surface-hover)]"
                    >
                      {/* The column that absorbs the slack, and the only one
                          allowed to truncate: two rows under the same bot are
                          told apart by nothing else, so it gets every pixel the
                          numbers do not need — and the row's `title` says it in
                          full when even that is not enough. */}
                      <td className="truncate py-0.5 pl-3 pr-1.5">
                        {leaf.label}
                      </td>
                      <td className="whitespace-nowrap px-1.5 py-0.5 text-[var(--color-text-muted)]">
                        {leaf.pair}
                      </td>
                      <td className="px-1.5 py-0.5 text-right font-mono tabular-nums">
                        {byController.get(leaf.id) ?? 0}
                      </td>
                      <td className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
                        {formatRuntimeHours(uptime)}
                      </td>
                      <td className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
                        {formatCompactVolume(money(leaf.volume), currencySymbol)}
                      </td>
                      <td
                        className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono tabular-nums"
                        style={{ color: pnlColor(realized) }}
                      >
                        {formatCurrencyPnl(realized, currencySymbol)}
                      </td>
                      <td
                        className="whitespace-nowrap px-1.5 py-0.5 text-right font-mono tabular-nums"
                        style={{ color: pnlColor(unrealized) }}
                      >
                        {formatCurrencyPnl(unrealized, currencySymbol)}
                      </td>
                      <td
                        className="whitespace-nowrap py-0.5 pl-1.5 pr-3 text-right font-mono font-medium tabular-nums"
                        style={{ color: pnlColor(net) }}
                      >
                        {formatCurrencyPnl(net, currencySymbol)}
                      </td>
                    </tr>
                  );
                })}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

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
