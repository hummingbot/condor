import type { QueryClient } from "@tanstack/react-query";

import { runLabel } from "@/components/agent/lab/runs";
import { fleetRows } from "@/components/agent/workspace/fleet";
import { poolLabel } from "@/components/dex/format";
import { readLpPosition } from "@/components/dex/lp-position";
import { getDisplayCurrency } from "@/hooks/useDisplayCurrency";
import { homeView } from "@/lib/homeView";
import type {
  AgentBrain,
  AgentDetail,
  AgentRunRow,
  AgentSummary,
  BotDetail,
  BotRunsResponse,
  BotsPageResponse,
  ConsolidatedPosition,
  ConsolidatedPositionsResponse,
  ControllerInfo,
  ExecutorInfo,
  PoolSummary,
  PortfolioHistoryResponse,
  PortfolioResponse,
  ReportGroup,
  RoutineInfo,
  RoutineInstance,
  StrategyDetail,
  StrategySummary,
} from "./api";
import {
  formatAge,
  formatCurrency,
  formatCurrencyPnl,
  formatCurrencyVolume,
  formatPct,
  formatPriceSig,
  isExecutorActive,
  toMs,
} from "./formatters";
import { runKeyLabel, type FleetOwner } from "./agent-attribution";
import { runStatus } from "./perf-tree";
import { formatWithRate, type RateTable } from "./rates";
import type { ViewFacts } from "./viewFacts";

/**
 * URL → what screen this is, what it is about, and what it currently says.
 *
 * The route baseline for the chat's page context: a flat table over the
 * routes in `App.tsx`, contributing a label and a URL-derived subject through
 * the same `useViewFacts` seam every richer contributor uses.
 *
 * With a `QueryClient` in hand it also fills in `onScreen` (FEAT-060) by
 * reading the very cache each page renders from — one lookup, in the file that
 * already owns route→label, instead of eight page components each handing
 * their state over and each a chance to go stale. Query keys are already
 * load-bearing shared contract here (`AgentChatTab` reuses `["agents"]` so
 * react-query dedupes against the fleet tab), so this adds another consumer of
 * a coupling that exists, not a new kind of it. `pageFacts.onScreen.test.ts`
 * seeds the real keys, so a rename breaks a test rather than an answer.
 *
 * Called at *send* time, never at render time: a cache read costs nothing
 * while the user is only looking at the page, and is current at the moment
 * they ask — including data that arrived after the bubble was opened.
 *
 * ## What earns a slot (FEAT-072)
 *
 * The cap is not the constraint — a generous block runs a fifth of it — so the
 * budget to spend carefully is the model's attention. A field that cannot
 * change an answer costs more than it is worth. Four questions a user standing
 * on a page actually asks, and four rules on how the answer is written:
 *
 * - **R1 — aggregate, then the exception.** Never just `3 running / 7`; name
 *   the ones that are not, capped at three plus `+N more`. The count orients;
 *   the names are what make the follow-up answerable with no second fetch.
 * - **R2 — no number without its comparison.** `pnl +$412` is unjudgeable;
 *   `+$412 · +$137/day` is. Rates come off real elapsed hours, never a
 *   division by "one day".
 * - **R3 — say what is filtered out.** Tab, page, active filters, and above
 *   all the denominator: `loaded 50 of 312`, never a bare `loaded 50`.
 * - **R4 — the notable rows are the screen.** Best and worst by PNL, by name:
 *   the highest fidelity per byte any list page can offer. Not the *visible*
 *   rows — the user can already see those.
 * - **R5 — identity, not ids.** `bot "backpack-mm-3"`. A model cannot speak an
 *   id back to a human.
 * - **R6 — stamp freshness only when it is stale.** `as of 4m ago` past 60s; a
 *   current number needs no stamp, and a four-minute-old poll presented as now
 *   is the failure the reporting rules exist to prevent.
 * - **R7 — ambient facts stay out.** Server, display currency, total equity are
 *   true on every page and belong in a layer this file is not.
 * - **R8 — headline here, table on demand.** Past ~5 rows it is not a view fact.
 * - **R9 — never collect credential-shaped fields.** `/settings` gathers none;
 *   `secrets.redact` guards the wire, but the block must not hold them either.
 *
 * And over all of it: **do not pad to fill**.
 */
export function routeFacts(
  pathname: string,
  search: string,
  qc?: QueryClient,
): ViewFacts | null {
  // The chat view of the home already *is* the chat; telling the agent "the
  // user is looking at a chat with you" is noise. Its other view is not
  // (FEAT-104) — the fleet overview is a page *about* the agents, so it falls
  // through to the `^/$` entry below and describes itself like any other.
  if (pathname === "/" && homeView(search) === "chat") return null;

  const params = new URLSearchParams(search);
  // `?view=` is the agent workspace's spelling of the same idea (FEAT-103):
  // one route whose section is a query parameter. Read here rather than in the
  // one entry that uses it, so a route never has to parse a search string.
  const tab = params.get("view") || params.get("tab") || "";

  for (const { pattern, facts, subject, onScreen } of ROUTES) {
    const m = pathname.match(pattern);
    if (!m) continue;
    const parts = m.slice(1).map(decode);
    const base = facts(parts, tab, params);
    if (!qc || (!onScreen && !subject)) return base;
    // A half-loaded cache must leave the block partial, never empty and never
    // thrown: the user asking mid-load still deserves the label and subject.
    try {
      const named = subject?.(parts, tab, qc, params);
      const shown = onScreen && prune(onScreen(parts, tab, qc, params));
      return {
        ...base,
        ...(named ? { subject: named } : {}),
        ...(shown ? { onScreen: shown } : {}),
      };
    } catch {
      return base;
    }
  }
  return null;
}

function decode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/** Drop the fields that did not resolve; `undefined` when none did. */
function prune(
  shown: ViewFacts["onScreen"],
): ViewFacts["onScreen"] | undefined {
  if (!shown) return undefined;
  const kept = Object.entries(shown).filter(
    ([, v]) => v !== null && v !== undefined && v !== "",
  );
  return kept.length > 0 ? Object.fromEntries(kept) : undefined;
}

// ── Reading the cache the page renders from ──

/**
 * The freshest cached entry under a key prefix.
 *
 * Every page-level key is server-scoped (`["portfolio", server]`) and the route
 * says nothing about which server is selected, so the prefix is matched rather
 * than the whole key and the most recently updated entry wins — which is the
 * one the screen in front of the user is rendering. `where` narrows to the
 * entry a route parameter names (a bot id, a pool address).
 *
 * Three things are read off the winner and all three matter: its payload, its
 * *key* (a page's own tab, network and filters live there rather than in what
 * came back) and when it last updated (R6).
 */
function pick(
  qc: QueryClient,
  prefix: unknown[],
  where?: (key: readonly unknown[]) => boolean,
): { data: unknown; key: readonly unknown[]; at: number } | undefined {
  let best: { data: unknown; key: readonly unknown[]; at: number } | undefined;
  for (const query of qc.getQueryCache().findAll({ queryKey: prefix })) {
    const { data, dataUpdatedAt } = query.state;
    if (data === undefined) continue;
    if (where && !where(query.queryKey)) continue;
    if (!best || dataUpdatedAt > best.at) {
      best = { data, key: query.queryKey, at: dataUpdatedAt };
    }
  }
  return best;
}

function fresh<T>(
  qc: QueryClient,
  prefix: unknown[],
  where?: (key: readonly unknown[]) => boolean,
): T | undefined {
  return pick(qc, prefix, where)?.data as T | undefined;
}

/** The key of the freshest cached entry under a prefix. */
function freshKey(
  qc: QueryClient,
  prefix: unknown[],
): readonly unknown[] | undefined {
  return pick(qc, prefix)?.key;
}

/**
 * `4m ago` when the poll behind a page has gone quiet, `undefined` while it is
 * current (R6).
 *
 * A number is only worth stamping once it might have moved: under a minute the
 * stamp is noise on every turn, and past it silence would be a claim the block
 * cannot back — the reporting rules exist because a stale figure presented as
 * current is worse than no figure.
 */
const STALE_AFTER_SEC = 60;

function asOf(
  qc: QueryClient,
  prefix: unknown[],
  where?: (key: readonly unknown[]) => boolean,
): string | undefined {
  const at = pick(qc, prefix, where)?.at;
  if (!at) return undefined;
  return (Date.now() - at) / 1000 > STALE_AFTER_SEC
    ? `${formatAge(at / 1000)} ago`
    : undefined;
}

/**
 * Money formatted the way the page formats it.
 *
 * Reads the same `["rates", server, currency, …]` cache `useRates` fills and
 * runs it through the same `lib/rates` rule — same conversion, same symbol,
 * same `⚠` marker — so the block says `$-412.30` where the screen says
 * `$-412.30`, in the display currency the user picked rather than in raw quote
 * units. Sharing the rule is the point: the copy that used to live here had
 * drifted on the symbol for an unconvertible quote (ARCH-228).
 */
function money(qc: QueryClient) {
  const currency = getDisplayCurrency();
  const table = fresh<RateTable>(qc, ["rates"], (key) => key[2] === currency);

  // A missing number is left out of the block entirely — better no fact than a
  // confident `$0.00` the screen is not showing.
  const format = (fmt: (val: number, symbol?: string) => string) => {
    const formatted = formatWithRate(fmt, table, currency);
    return (val: number | null | undefined, quote?: string): string | undefined =>
      val == null || !Number.isFinite(val) ? undefined : formatted(val, quote);
  };

  return {
    value: format(formatCurrency),
    pnl: format(formatCurrencyPnl),
    volume: format(formatCurrencyVolume),
    currency,
  };
}

/** `3 running / 5` — the shape a user says out loud about a list. */
function ratio(running: number, total: number): string {
  return `${running} running / ${total}`;
}

/**
 * `mm-sol-2, grid-eth-1 +2 more` — R1's exception list.
 *
 * Capped at three because the point is to make the follow-up answerable, not to
 * transcribe the table: past a handful of names it is R8's territory.
 */
function names(list: string[], cap = 3): string | undefined {
  const clean = list.filter(Boolean);
  if (clean.length === 0) return undefined;
  const head = clean.slice(0, cap).join(", ");
  return clean.length > cap ? `${head} +${clean.length - cap} more` : head;
}

/**
 * `+$137/day` from a real elapsed span, or nothing (R2).
 *
 * Extrapolated from the hours actually run, never by dividing by "one day" —
 * a bot two hours old has not made a day's PNL, and saying so is how the block
 * turns an unjudgeable total into a rate. Under an hour there is nothing
 * honest to extrapolate from, so the field is simply absent.
 */
function perDay(
  total: number | null | undefined,
  sinceMs: number | undefined,
  fmt: (val: number, quote?: string) => string | undefined,
  quote?: string,
): string | undefined {
  if (total == null || !Number.isFinite(total) || !sinceMs) return undefined;
  const hours = (Date.now() - sinceMs) / 3_600_000;
  if (!(hours >= 1)) return undefined;
  const rate = fmt((total / hours) * 24, quote);
  return rate ? `${rate}/day` : undefined;
}

/**
 * The best and worst of a ranked list, as `name value` (R4/R5).
 *
 * One helper because every list page answers the same question — "how is this
 * going" is best and worst, by name — and three copies of the sort is three
 * chances for one of them to rank the wrong way.
 */
type Ranked = { name: string; value: number; quote?: string };

function extremes(
  rows: Ranked[],
  fmt: (val: number, quote?: string) => string | undefined,
  want = 1,
): { best?: string; worst?: string } {
  // A row that made and lost nothing is neither the best nor the worst news on
  // the screen; on a list where most rows are flat it would fill both ends with
  // `+$0.00` and say nothing. R0: do not pad to fill.
  const moved = rows.filter((r) => Number.isFinite(r.value) && r.value !== 0);
  if (moved.length < 2) return {};
  // Never let the two ends overlap: three rows asked for "top and bottom 3"
  // would otherwise be listed twice, once in each direction.
  const take = Math.max(1, Math.min(want, Math.floor(moved.length / 2)));
  const sorted = [...moved].sort((a, b) => b.value - a.value);
  // Each row is formatted in *its own* quote: one screen can hold BRL, USDT and
  // USDC rows, and labelling a BRL total with the display currency's symbol is
  // a wrong number, not a formatting detail (ARCH-228).
  const label = (row: Ranked) => `${row.name} ${fmt(row.value, row.quote) ?? row.value}`;
  return {
    best: names(sorted.slice(0, take).map(label), take),
    worst: names(sorted.slice(-take).reverse().map(label), take),
  };
}

/** `grid 12, position 3` — what a mixed list is actually made of. */
function byType(rows: { type: string }[], cap = 2): string | undefined {
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (row.type) counts.set(row.type, (counts.get(row.type) ?? 0) + 1);
  }
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  return names(
    ranked.map(([type, n]) => `${type} ${n}`),
    cap,
  );
}

/**
 * What each `?view=` is called, for the one route that has nine of them.
 *
 * Absent from the table means the workspace's own name: `now` is the default
 * view and every Knowledge section is still "the agent", read one way or
 * another. The four that get their own label are the ones a reader would not
 * recognise under it.
 */
const VIEW_LABELS: Record<string, string> = {
  runs: "Agent runs",
  tick: "Agent tick",
  playbook: "Strategy playbook",
  money: "Strategy performance",
  fleet: "Agent fleet",
};

/** The views the run index is actually on screen for. */
const RUN_VIEWS = new Set(["runs", "tick", "fleet"]);

/** The agent itself: who it is, what is looping, and what that has made. */
function agentIdentityFacts(
  agent: AgentDetail,
  slug: string,
  qc: QueryClient,
): NonNullable<ViewFacts["onScreen"]> {
  const m = money(qc);
  const strategies = agent.strategies ?? [];
  const live = strategies.filter((s) => s.instances.length > 0);
  const sum = (of: (s: StrategySummary) => number) =>
    strategies.length > 0 ? strategies.reduce((n, s) => n + (of(s) || 0), 0) : undefined;
  // Only loaded once the knowledge sections have been opened, so absent is the
  // normal case and `prune` drops it rather than reporting zero libraries.
  const brain = fresh<AgentBrain>(qc, ["agent-brain"], (key) => key[1] === slug);
  return {
    agent: agent.name,
    model: agent.agent_key || undefined,
    strategies: ratio(live.length, strategies.length),
    running: names(
      live.map((s) => `${s.name} (tick ${s.instances[0]?.tick_count ?? 0})`),
    ),
    "daily pnl": m.pnl(sum((s) => s.daily_pnl)),
    "total pnl": m.pnl(sum((s) => s.total_pnl)),
    "open positions": sum((s) => s.open_positions),
    skills: brain?.skills?.length,
    memories: brain?.memories?.length,
    server: agent.server_name || undefined,
    "as of": asOf(qc, ["agent"], (key) => key[1] === slug),
  };
}

/**
 * The strategy in scope, when `?strategy=` names one.
 *
 * Spread over the agent's own numbers on purpose: with a scope selected, the
 * money on screen is this strategy's, and reporting the agent's total beside it
 * under the same key would be the block contradicting the page.
 */
function strategyScopeFacts(
  slug: string,
  sslug: string,
  agent: AgentDetail | undefined,
  qc: QueryClient,
): NonNullable<ViewFacts["onScreen"]> | undefined {
  const detail = fresh<StrategyDetail>(
    qc,
    ["strategy"],
    (key) => key[1] === slug && key[2] === sslug,
  );
  const summary: StrategySummary | undefined = (agent?.strategies ?? []).find(
    (s) => s.slug === sslug,
  );
  if (!detail && !summary) return undefined;
  const m = money(qc);
  const instances = detail?.instances ?? summary?.instances ?? [];
  const live = instances[0];
  return {
    strategy: detail?.name || summary?.name,
    status: detail?.status || summary?.status,
    running: live
      ? `session ${live.session_num}, tick ${live.tick_count} (${live.execution_mode})`
      : "no live instance",
    model: live?.agent_key,
    "daily pnl": m.pnl(summary?.daily_pnl ?? live?.daily_pnl),
    "total pnl": m.pnl(summary?.total_pnl ?? live?.total_pnl),
    "open positions": summary?.open_positions ?? live?.open_count,
    sessions: detail?.sessions?.length,
    experiments: detail?.experiments?.length,
    "as of": asOf(qc, ["strategy"], (key) => key[1] === slug && key[2] === sslug),
  };
}

/**
 * The runs, for the views that show them.
 *
 * Scoped to `?strategy=` when there is one, because that is what the rail is
 * filtered to. No money here on purpose: the run index is priced by nothing,
 * and reporting a zero it never computed is the defect the runs view exists to
 * remove.
 */
function agentRunsFacts(
  slug: string,
  sslug: string | null,
  qc: QueryClient,
): NonNullable<ViewFacts["onScreen"]> | undefined {
  const all = fresh<AgentRunRow[]>(qc, ["agent-runs"], (key) => key[1] === slug);
  if (!all) return undefined;
  const runs = sslug ? all.filter((r) => r.strategy_slug === sslug) : all;
  const sessions = runs.filter((r) => r.kind === "session");
  const dryRuns = runs.filter((r) => r.kind === "experiment");
  const live = runs.filter((r) => r.status === "running" || r.status === "paused");
  const failed = runs.filter((r) => r.error);
  const newest = runs[0];
  return {
    runs: runs.length,
    sessions: sessions.length,
    "dry runs": dryRuns.length || undefined,
    // R1: the count orients, the names make the follow-up answerable.
    live: names(live.map((r) => `${runLabel(r)} ${r.strategy_name} (tick ${r.tick_count})`)),
    failed: names(failed.map((r) => `${runLabel(r)} ${r.strategy_name}`)),
    newest: newest
      ? `${runLabel(newest)} ${newest.strategy_name}, ${newest.tick_count} ticks${
          newest.started_at ? `, started ${formatAge(newest.started_at)} ago` : ""
        }`
      : undefined,
    "total ticks": runs.reduce((n, r) => n + r.tick_count, 0) || undefined,
    "as of": asOf(qc, ["agent-runs"], (key) => key[1] === slug),
  };
}

// ── The table ──

type Reader = (
  parts: string[],
  tab: string,
  qc: QueryClient,
  /** The rest of the query string, for a route whose scope lives in one. */
  params: URLSearchParams,
) => ViewFacts["onScreen"];

/**
 * The finished runs, which carry their own denominator (R3).
 *
 * Read for every `/bots` screen now rather than for a tab of its own: the run
 * history is the Terminated population's own branch of the tree (FEAT-086), and
 * it is loaded only while that population is selected — so an absent cache
 * entry is "the reader is on Running", which is why this contributes nothing
 * rather than zeroes when there is none.
 */
/**
 * The agents with trading on this fleet, and which of their loops are alive.
 *
 * Read from the `["fleet-map"]` cache the page already polls, so this costs
 * nothing and is exactly as current as the sidebar. `undefined` when the map is
 * empty or was never fetched — a server with no agents should say nothing about
 * agents rather than say "0".
 */
function agentFacts(qc: QueryClient): string | undefined {
  const owners = fresh<FleetOwner[]>(qc, ["fleet-map"]);
  if (!Array.isArray(owners) || owners.length === 0) return undefined;
  const live = owners.filter((owner) => owner.live);
  const named = names(owners.map((owner) => runKeyLabel(owner.runKey)));
  return live.length > 0
    ? `${named} · ${live.length} looping (${names(live.map((owner) => `${runKeyLabel(owner.runKey)} ${owner.live?.status}`))})`
    : `${named} · none looping`;
}

function runsFacts(qc: QueryClient): ViewFacts["onScreen"] {
  const data = fresh<BotRunsResponse>(qc, ["bot-runs"]);
  if (!data) return undefined;
  const m = money(qc);
  const runs = data.runs ?? [];
  // Counted through `runStatus`, the same derivation the sidebar dot and the
  // scope header read, so the block and the screen say the same word about the
  // same run. `run_status` itself cannot be counted: upstream leaves it at
  // `CREATED` for a bot that is trading right now (FEAT-089).
  const statuses = new Map<string, number>();
  for (const r of runs) {
    const status = runStatus(r);
    if (status) statuses.set(status, (statuses.get(status) ?? 0) + 1);
  }
  const { best, worst } = extremes(
    runs.map((r) => ({ name: r.bot_name, value: r.global_pnl_quote ?? 0 })),
    m.pnl,
  );
  // Named `… run` rather than `best`/`worst`: these are merged into the `/bots`
  // entry, which already ranks *bots* under those two keys, and a bare `best`
  // here would silently replace it with an answer to a different question.
  //
  // The per-status breakdown the runs table carried is gone from the block, not
  // from the screen. Everything here shares one budget with the fleet's facts
  // *and* with what the reader actually chose (population, scope, filters), and
  // that last group is the half no cache can reconstruct — so it is the half
  // that must not be the one truncated away.
  return {
    runs: `${runs.length} of ${data.total ?? runs.length}`,
    "runs stopped": statuses.get("stopped"),
    "best run": best,
    "worst run": worst,
  };
}

const ROUTES: {
  pattern: RegExp;
  facts: (parts: string[], tab: string, params: URLSearchParams) => ViewFacts;
  /** A subject only the cache can name — R5 wants `"backpack-mm-3"`, not `42`. */
  subject?: (
    parts: string[],
    tab: string,
    qc: QueryClient,
    params: URLSearchParams,
  ) => string | undefined;
  onScreen?: Reader;
}[] = [
  {
    /**
     * The fleet overview — the home's other view (FEAT-104).
     *
     * Reached by a bare `/` since FEAT-104 step 3, and by an explicit
     * `?view=fleet`: the chat view returns above, before this table is walked.
     *
     * Read through `fleetRows`, the page's own rule, rather than by summing
     * the payload again here — including its dash: an agent with nothing
     * attributed is *counted* as unattributed instead of being folded into the
     * net as a zero, which is the same honesty the row prints.
     */
    pattern: /^\/$/,
    facts: () => ({ label: "Fleet overview" }),
    onScreen: (_parts, _view, qc) => {
      const agents = fresh<AgentSummary[]>(qc, ["agents"]);
      if (!Array.isArray(agents) || agents.length === 0) return undefined;
      const m = money(qc);
      const rows = fleetRows(agents, Date.now() / 1000);
      const looping = rows.filter((row) => row.live?.status === "running");
      const attributed = rows.filter((row) => row.net !== null);
      const net = attributed.reduce((sum, row) => sum + (row.net ?? 0), 0);
      const { best, worst } = extremes(
        attributed.map((row) => ({ name: row.name, value: row.net ?? 0 })),
        m.pnl,
      );
      const wants = rows.filter((row) => row.alerts.length > 0);
      return {
        agents: ratio(looping.length, rows.length),
        looping: names(
          looping.map((row) => `${row.name} tick ${row.live?.tick_count ?? 0}`),
        ),
        "attributed net": attributed.length > 0 ? m.pnl(net) : undefined,
        unattributed: rows.length - attributed.length || undefined,
        best,
        worst,
        // R4, and the reason a reader opened this page at all.
        "wants a person":
          names(wants.map((row) => `${row.name}: ${row.alerts[0].text}`), 2) ||
          undefined,
      };
    },
  },
  {
    pattern: /^\/portfolio$/,
    facts: () => ({ label: "Portfolio" }),
    onScreen: (_p, tab, qc) => {
      const m = money(qc);
      const data = fresh<PortfolioResponse>(qc, ["portfolio"]);
      const history = fresh<PortfolioHistoryResponse>(qc, ["portfolio-history"]);
      // The window is the one the period selector is on, and it lives in the
      // key — captioning it "24h" would put a label on screen that is not.
      const window = String(freshKey(qc, ["portfolio-history"])?.[2] ?? "");
      const points = history?.points ?? [];
      const first = points[0];
      const last = points[points.length - 1];
      const change =
        points.length >= 2 ? last.total_usd - first.total_usd : null;
      const held = fresh<ConsolidatedPositionsResponse>(qc, [
        "consolidated-positions",
      ]);
      const positions: ConsolidatedPosition[] = held
        ? [...(held.executor_positions ?? []), ...(held.bot_positions ?? [])]
        : [];
      // Nothing fetched yet is a blank screen: naming the tab and the currency
      // of numbers that are not there would describe a page the user cannot
      // read. Label and subject alone are the honest answer until data lands.
      if (!data && !history && !held) return undefined;

      const connectors = data?.connectors ?? [];
      const venues = [...connectors]
        .sort((a, b) => b.total_usd - a.total_usd)
        .slice(0, 3)
        .map((c) => `${c.connector} ${m.value(c.total_usd) ?? "—"}`);

      // One token can sit on three venues; what the user holds is the sum.
      const held_by_token = new Map<string, number>();
      for (const c of connectors) {
        for (const b of c.balances ?? []) {
          if (!b.token || !Number.isFinite(b.usd_value)) continue;
          held_by_token.set(b.token, (held_by_token.get(b.token) ?? 0) + b.usd_value);
        }
      }
      const total = data?.total_usd ?? 0;
      const holdings = [...held_by_token.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([token, usd]) => {
          const share = total > 0 ? ` (${Math.round((usd / total) * 100)}%)` : "";
          return `${token} ${m.value(usd) ?? "—"}${share}`;
        });

      // Which holding moved the portfolio the wrong way over the window on
      // screen — the question a red total prompts, and the one the totals
      // alone cannot answer.
      let worst: string | undefined;
      if (first?.tokens && last?.tokens) {
        const moves = Object.keys(last.tokens)
          .map((token) => ({
            token,
            delta: (last.tokens![token] ?? 0) - (first.tokens![token] ?? 0),
          }))
          .sort((a, b) => a.delta - b.delta);
        if (moves.length > 0 && moves[0].delta < 0) {
          worst = `${moves[0].token} ${m.pnl(moves[0].delta) ?? ""}`.trim();
        }
      }

      const onPositions = tab === "positions";
      const largest = [...positions].sort(
        (a, b) => (b.notional_value ?? 0) - (a.notional_value ?? 0),
      )[0];
      return {
        total: m.value(data?.total_usd),
        [window ? `change (${window})` : "change"]: m.pnl(change),
        currency: data ? m.currency : undefined,
        assets: data?.connectors.reduce((n, c) => n + c.balances.length, 0),
        venues: names(venues),
        "top holdings": names(holdings),
        [window ? `worst mover (${window})` : "worst mover"]: worst,
        "open positions": held ? positions.length : undefined,
        unrealized: onPositions
          ? m.pnl(positions.reduce((n, p) => n + (p.unrealized_pnl ?? 0), 0))
          : undefined,
        "largest position": onPositions && largest
          ? `${largest.trading_pair} ${largest.position_side} ${m.value(largest.notional_value) ?? ""}`.trim()
          : undefined,
        tab: onPositions ? "positions" : "assets",
        "as of": asOf(qc, ["portfolio"]),
      };
    },
  },
  {
    pattern: /^\/bots$/,
    // One label, whatever the query string says. `/bots` is the performance
    // browser and nothing else: the tab bar went in FEAT-084, and the two tabs
    // that outlived it — Runs and Archived — are the Terminated population that
    // `Bots.tsx` redirects them into (FEAT-086). It is also the label
    // `PerfBrowser` contributes under, so the cache's half of this screen and
    // the reader's half render as one screen instead of two.
    facts: () => ({ label: "Bots" }),
    onScreen: (_p, _tab, qc) => {
      const data = fresh<BotsPageResponse>(qc, ["bots"]);
      if (!data) return undefined;
      const m = money(qc);
      const bots = data.bots ?? [];
      const controllers = data.controllers ?? [];
      const running = bots.filter((b) => b.status === "running");

      // Per-bot PNL is a groupBy over the controller rows the page already
      // holds — the fleet payload totals the whole server but ranks nothing.
      // Summing across pairs mixes quotes, which is the same approximation the
      // executors KPI strip makes and cannot change *which* bot is losing.
      const perBot = new Map<string, { value: number; quote?: string }>();
      for (const c of controllers) {
        if (!c.bot_name) continue;
        const row = perBot.get(c.bot_name) ?? {
          value: 0,
          quote: c.trading_pair?.split("-")[1],
        };
        row.value += c.global_pnl_quote ?? 0;
        perBot.set(c.bot_name, row);
      }
      const { best, worst } = extremes(
        [...perBot.entries()].map(([name, row]) => ({ name, ...row })),
        m.pnl,
      );

      // The executors the browser hangs under those controllers. The page holds
      // the whole bounded walk — live and archived alike — under the key the
      // retired `/executors` screen used, which is why that key is read here
      // rather than being orphaned by its deletion (FEAT-072, FEAT-086).
      const paged = fresh<{ pages: { executors: ExecutorInfo[]; next_cursor: string | null }[] }>(
        qc,
        ["executors-infinite"],
      );
      const loaded = paged?.pages?.flatMap((p) => p.executors ?? []) ?? [];
      const activeExecutors = loaded.filter((ex) => isExecutorActive(ex.status));
      // The walk answers with a cursor, not a count, so the honest total is
      // "all of them" when it finished and "more than this" when it stopped at
      // the cap — never a bare number an aggregate could be read off.
      const more = paged?.pages?.[paged.pages.length - 1]?.next_cursor != null;

      // Ranked by market and type rather than row by row: an executor has no
      // name, so three top rows come back as the same `SOL-USDC grid_executor`
      // three times — three slots that say one thing. Grouped, the same bytes
      // name which market is losing. Kept under its own keys beside the per-bot
      // ranking above, which answers a different question.
      const perMarket = new Map<string, { value: number; quote?: string }>();
      for (const ex of loaded) {
        const name = `${ex.trading_pair} ${ex.type}`;
        const row = perMarket.get(name) ?? { value: 0, quote: ex.trading_pair?.split("-")[1] };
        row.value += ex.pnl ?? 0;
        perMarket.set(name, row);
      }
      // Two apiece rather than three: this entry now carries the fleet's own
      // ranking beside it, and the rendered block has a budget to stay inside.
      const markets = extremes(
        [...perMarket.entries()].map(([name, row]) => ({ name, ...row })),
        m.pnl,
        2,
      );

      // Which population and scope the reader is actually on is the browser's
      // own contribution (`useViewFacts`), merged under this same label.
      return {
        bots: ratio(running.length, bots.length),
        stopped: names(
          bots.filter((b) => b.status !== "running").map((b) => b.bot_name),
        ),
        controllers: controllers.length,
        // Server-side and already converted, unlike the groupBy above.
        "total pnl": m.pnl(data.total_pnl),
        "total volume": m.volume(data.total_volume),
        best,
        worst,
        "active executors": activeExecutors.length || undefined,
        // Per-quote conversion is what the KPI strip does; the quotes on one
        // screen are near-always the same, so summing after conversion here
        // reproduces it without a second pass over the rows.
        "active pnl": activeExecutors.length
          ? m.pnl(
              activeExecutors.reduce((total, ex) => total + (ex.pnl ?? 0), 0),
              activeExecutors[0]?.trading_pair?.split("-")[1],
            )
          : undefined,
        "executors by type": loaded.length ? byType(loaded) : undefined,
        "executors loaded": loaded.length
          ? more
            ? `${loaded.length} of more — not all loaded`
            : `${loaded.length} of ${loaded.length}`
          : undefined,
        "best market": markets.best,
        "worst market": markets.worst,
        // Who on this fleet is an agent's, from the map the browser polls
        // (FEAT-096). Named rather than counted: "which agents are trading" is
        // a question about identities, and three names answer it where a
        // number does not. The reader's own agent *scope* is the browser's half
        // of this entry (`useViewFacts`), merged under the same label.
        agents: agentFacts(qc),
        // Only loaded while the Terminated population is selected, so its
        // absence says the reader is on Running rather than that there are none.
        ...(runsFacts(qc) ?? {}),
        "as of": asOf(qc, ["bots"]),
      };
    },
  },
  {
    pattern: /^\/bots\/([^/]+)$/,
    facts: ([id]) => ({ label: "Bot detail", subject: `bot id ${id}` }),
    subject: ([id], _tab, qc) => {
      const bot = fresh<BotDetail>(qc, ["bot"], (key) => key[2] === id)?.bot;
      return bot?.name ? `bot "${bot.name}" (id ${id})` : undefined;
    },
    onScreen: ([id], _tab, qc) => {
      const detail = fresh<BotDetail>(qc, ["bot"], (key) => key[2] === id);
      const bot = detail?.bot;
      if (!bot) return undefined;
      const m = money(qc);
      const quote = bot.trading_pair?.split("-")[1] || "USDT";
      // The bot's own controllers, from the fleet listing the /bots page
      // already holds — the detail payload counts none.
      const fleet = fresh<BotsPageResponse>(qc, ["bots"]);
      const summary = fleet?.bots?.find((b) => b.bot_name === bot.name);
      const controllers: ControllerInfo[] = (fleet?.controllers ?? []).filter(
        (c) => c.bot_name === bot.name,
      );
      const sum = (of: (c: ControllerInfo) => number) =>
        controllers.length > 0 ? controllers.reduce((n, c) => n + (of(c) || 0), 0) : undefined;
      const since = summary?.deployed_at ? toMs(summary.deployed_at) : undefined;
      // A controller's `status` is a hardcoded "running" in this payload; what
      // actually stops one is the kill switch in its config, which is what the
      // fleet table reads to grey a row out.
      const killed = controllers
        .filter((c) => (c.config as { manual_kill_switch?: boolean })?.manual_kill_switch === true)
        .map((c) => c.controller_id || c.controller_name);
      return {
        bot: bot.name,
        status: bot.status,
        pnl: m.pnl(bot.pnl, quote),
        "pnl rate": perDay(bot.pnl, since, m.pnl, quote),
        realized: m.pnl(sum((c) => c.realized_pnl_quote), quote),
        unrealized: m.pnl(sum((c) => c.unrealized_pnl_quote), quote),
        volume: m.volume(sum((c) => c.volume_traded), quote),
        pair: bot.trading_pair,
        controllers: summary?.num_controllers ?? (controllers.length || undefined),
        "stopped controllers": names(killed),
        uptime: since ? formatAge(since / 1000) : undefined,
        "as of": asOf(qc, ["bot"], (key) => key[2] === id),
      };
    },
  },
  // `/trade`'s form is local component state, so `CreateExecutor` registers it
  // through `useViewFacts` — no cache holds what the user has typed. Both
  // contributors carry the label "Trade" so they render as one screen.
  { pattern: /^\/trade$/, facts: () => ({ label: "Trade" }) },
  {
    pattern: /^\/dex$/,
    facts: () => ({ label: "DEX pools" }),
    onScreen: (_p, _tab, qc) => {
      // The browser's chain and source tab are local state, but the listing it
      // asked for carries both in its key: ["dex-pools", server, kind, view,
      // network, query, dexes, page].
      const key = freshKey(qc, ["dex-pools"]);
      const page = fresh<{ pools: PoolSummary[] }>(qc, ["dex-pools"]);
      if (!key || !page) return undefined;
      const m = money(qc);
      const source = String(key[3] || key[2] || "");
      const pools = page.pools ?? [];
      const top = [...pools]
        .sort((a, b) => (b.reserve_usd ?? 0) - (a.reserve_usd ?? 0))
        .slice(0, 3)
        .map(
          (p) =>
            `${poolLabel(p)} tvl ${m.value(p.reserve_usd) ?? "—"} vol ${m.volume(p.volume_24h) ?? "—"}`,
        );
      return {
        network: String(key[4] || pools[0]?.network || ""),
        source,
        // R3: the box the user typed in and the venue chips they ticked are
        // what makes this listing a slice rather than "the pools".
        search: String(key[5] || ""),
        dexes: String(key[6] || ""),
        "pools listed": pools.length,
        page: Number(key[7]) > 1 ? Number(key[7]) : undefined,
        "top pools": names(top),
        "as of": asOf(qc, ["dex-pools"]),
      };
    },
  },
  {
    pattern: /^\/dex\/([^/]+)\/([^/]+)$/,
    facts: ([network, address]) => ({
      label: "DEX pool",
      subject: `pool ${address} on ${network}`,
    }),
    subject: ([network, address], _tab, qc) => {
      const pool = fresh<PoolSummary | null>(
        qc,
        ["dex-pool-by-address"],
        (key) => key[2] === network && key[3] === address,
      );
      return pool ? `the ${poolLabel(pool)} pool on ${pool.dex_id} (${network})` : undefined;
    },
    onScreen: ([network, address], _tab, qc) => {
      const pool = fresh<PoolSummary | null>(
        qc,
        ["dex-pool-by-address"],
        (key) => key[2] === network && key[3] === address,
      );
      if (!pool) return undefined;
      const m = money(qc);
      // The ranges Condor holds *in this pool* — the LP executors the pool
      // page's own position panel reads, filtered to this address, and read
      // through the same `readLpPosition` so the block and the panel cannot
      // disagree about whether a range is in range.
      const lp = fresh<ExecutorInfo[]>(qc, ["dex-lp-executors"]) ?? [];
      const mine = lp
        .filter((ex) => isExecutorActive(ex.status))
        .map(readLpPosition)
        .filter((pos) => pos && pos.poolAddress === pool.address)
        .map((pos) => pos!);
      const inRange = mine.filter((p) => p.state.toUpperCase() === "IN_RANGE").length;
      const value = mine.reduce((n, p) => n + (p.valueQuote ?? 0), 0);
      const fees = mine.reduce((n, p) => n + (p.feesQuote ?? 0), 0);
      const quote = pool.quote_symbol;
      return {
        pair: poolLabel(pool),
        dex: pool.dex_id,
        price: pool.current_price != null ? formatPriceSig(pool.current_price) : undefined,
        tvl: m.value(pool.reserve_usd),
        "24h volume": m.volume(pool.volume_24h),
        "24h change": pool.price_change_24h != null ? formatPct(pool.price_change_24h / 100) : undefined,
        "your lp positions": mine.length
          ? `${mine.length} (${inRange} in range, ${mine.length - inRange} out)`
          : undefined,
        "your lp value": mine.length ? m.value(value, quote) : undefined,
        "fees earned": mine.length ? m.value(fees, quote) : undefined,
        "as of": asOf(qc, ["dex-pool-by-address"], (key) => key[2] === network && key[3] === address),
      };
    },
  },
  {
    pattern: /^\/routines$/,
    facts: (_p, tab) => ({
      label: tab === "reports" ? "Routine reports" : "Routines",
    }),
    onScreen: (_p, tab, qc) => {
      const routines = fresh<RoutineInfo[]>(qc, ["routines"]);
      const instances = fresh<RoutineInstance[]>(qc, ["routine-instances"]) ?? [];
      const groups = fresh<ReportGroup[]>(qc, ["reports-grouped"]);
      if (!routines && !groups) return undefined;
      const running = instances.filter((i) => i.status === "running");
      const scheduled = instances.filter((i) => i.status === "scheduled");
      const live = running.length + scheduled.length;
      // The run that broke is the one worth naming, and the block is the only
      // place a user gets it without opening the instance.
      const failed = instances.find((i) => i.error);
      return tab === "reports"
        ? {
            "report sources": groups?.length,
            reports: groups?.reduce((n, g) => n + g.total_count, 0),
            "as of": asOf(qc, ["reports-grouped"]),
          }
        : {
            routines: routines?.length,
            instances: ratio(live, instances.length),
            running: names(
              running.map(
                (i) =>
                  `${i.routine_name}${i.last_run_at ? ` (last run ${formatAge(i.last_run_at)} ago)` : ""}`,
              ),
            ),
            scheduled: names(scheduled.map((i) => i.routine_name)),
            "last failure": failed
              ? `${failed.routine_name}: ${String(failed.error).split("\n")[0].slice(0, 120)}`
              : undefined,
            "as of": asOf(qc, ["routine-instances"]),
          };
    },
  },
  {
    /**
     * The agent workspace — one route, and `?view=` says which of its nine
     * sections is on screen (FEAT-103).
     *
     * This is three entries folded into one. `/agents/:slug/runs` (the Lab) and
     * `/agents/:slug/strategies/:sslug` (the strategy page) both redirect here
     * now, so a block written for either pattern would never be reached — and,
     * worse, without the fold the chat would describe every one of the nine
     * views as the same page.
     *
     * What is read follows what is shown: the agent always, the strategy when
     * one is in scope, the runs when the reader is looking at them. A page that
     * reports facts nobody can see on it is padding, and R-do-not-pad is the
     * rule over all of them.
     */
    pattern: /^\/agents\/([^/]+)$/,
    facts: ([slug], view, params) => {
      const sslug = params.get("strategy");
      return {
        label: VIEW_LABELS[view] ?? "Agent workspace",
        subject: sslug
          ? `strategy "${sslug}" of agent "${slug}"`
          : `agent "${slug}"`,
      };
    },
    subject: ([slug], _view, qc, params) => {
      const sslug = params.get("strategy");
      if (!sslug) return undefined;
      const detail = fresh<StrategyDetail>(
        qc,
        ["strategy"],
        (key) => key[1] === slug && key[2] === sslug,
      );
      return detail?.name
        ? `strategy "${detail.name}" of agent "${slug}"`
        : undefined;
    },
    onScreen: ([slug], view, qc, params) => {
      const agent = fresh<AgentDetail>(qc, ["agent"], (key) => key[1] === slug);
      const sslug = params.get("strategy");
      const base = agent ? agentIdentityFacts(agent, slug, qc) : undefined;
      const scoped = sslug ? strategyScopeFacts(slug, sslug, agent, qc) : undefined;
      const runs = RUN_VIEWS.has(view) ? agentRunsFacts(slug, sslug, qc) : undefined;
      if (!base && !scoped && !runs) return undefined;
      return { ...base, ...scoped, ...runs };
    },
  },
  // R9: nothing is read off this page. The wire is guarded by `secrets.redact`,
  // but a block that never gathers a credential-shaped field cannot leak one.
  { pattern: /^\/settings$/, facts: () => ({ label: "Settings" }) },
];
