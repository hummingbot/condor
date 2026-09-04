import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { FloorBreakdowns } from "@/components/agent/floor/FloorBreakdowns";
import { FloorChart } from "@/components/agent/floor/FloorChart";
import { FloorRows } from "@/components/agent/floor/FloorRows";
import { FloorStrip } from "@/components/agent/floor/FloorStrip";
import {
  groupSpine,
  mergeSlices,
  partitionFloor,
  type FloorSlice,
} from "@/components/agent/floor/floor";
import {
  fleetRows,
  floorTargets,
  foldRows,
  type FoldTarget,
} from "@/components/agent/workspace/fleet";
import { useFleetData } from "@/hooks/useFleetData";
import { useSeconds } from "@/hooks/useSeconds";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { mergeOwnerRows, ownerSeries, type SeriesOwner } from "@/lib/owner-series";
import { quoteConverter, runningLeaves } from "@/lib/perf-population";
import { UNKNOWN_LABEL } from "@/lib/perf-tree";

/**
 * Every agent's trading, added up on one floor (FEAT-112).
 *
 * Not a section of `/fleet`. That page is deliberately *per agent* — one row,
 * one scoped strategy, one link out. The floor asks what it cannot: not "what
 * is each agent doing" but **what do they add up to**, which needs a chart, a
 * fold of the whole population and breakdowns that cut across every agent at
 * once.
 *
 * There is **one fold on this page, read four ways.** The rows are `reconcile`
 * called through `foldRows` — the one producer ARCH-324 established — the strip
 * is `Σ` over the rows and the named non-agent parts, the breakdowns are slices
 * of the same accounting spine folded by the same `foldLeaves` with the same
 * `ConvertQuote`, and the chart is one `aggregatePnlSeries` call per part. The
 * fleet total equals the sum of its parts by construction rather than by
 * assertion, because they are literally the same leaves.
 *
 * Four things this page **names instead of inventing**, because the records
 * cannot say them: margin, leverage and account health (there is no accounts
 * route); live orders and fill-level flow (`fetch_active_orders` is unexposed
 * and carries no controller id to attribute by); sub-account nesting (neither
 * `ControllerInfo` nor `ExecutorInfo` carries an account, and `/portfolio`
 * flattens `account_name` away); and a complete fee total (`leafFromController`
 * hardcodes `fees: 0`, so what is measurable is a floor and is captioned as
 * one).
 *
 * Full bleed: it owns its scrolling, one body under a sticky strip. Every
 * request it makes is one the fleet browser already makes, under the same query
 * keys — a reader with `/bots` open pays nothing extra for it.
 */
export function Floor() {
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
    refetchInterval: 10000,
  });
  const { server: ambient } = useServer();
  const [params, setParams] = useSearchParams();

  // A mount-time clock, as the Money view takes it: nothing on this page counts
  // down, and a ticking one would re-fold every server every second.
  const now = useSeconds(false);

  // Ranked by the run rollup, which arrives synchronously with `["agents"]`,
  // and *displaying* the fold, which arrives per server (`fleet.ts:137-148`).
  // Sorting on the fold would reorder the list under the reader's cursor as
  // each server replied.
  const ranked = useMemo(() => fleetRows(agents, now / 1000), [agents, now]);
  const named = useMemo(
    () => ranked.map((row) => ({ slug: row.slug, name: row.name })),
    [ranked],
  );

  const groups = useMemo(() => floorTargets(agents, ambient), [agents, ambient]);
  const [byServer, setByServer] = useState<Record<string, FloorSlice>>({});

  // Kept keyed by server, never merged on arrival — `FleetOverview.tsx:94`'s
  // rule, for the same reason here: a server dropping out of `groups` must take
  // its numbers with it rather than leave a stale fold under an agent that
  // moved.
  const report = useCallback(
    (server: string, slice: FloorSlice) =>
      setByServer((prev) =>
        prev[server] === slice ? prev : { ...prev, [server]: slice },
      ),
    [],
  );
  const slices = useMemo(
    () =>
      groups
        .map(({ server }) => byServer[server])
        .filter((slice): slice is FloorSlice => !!slice),
    [groups, byServer],
  );

  const model = useMemo(() => mergeSlices(slices, named), [slices, named]);

  /**
   * Every server's series onto one timeline, merged once and read twice — by
   * the chart and by the rows' sparklines, so a row and its line are the same
   * series rather than two readings of one.
   *
   * The owner order is the rows band's: the agents in the ranked order, then
   * the named non-agent parts. That is what makes a colour mean the same thing
   * in the legend, on the line and beside the row.
   */
  const chart = useMemo(() => {
    const order = [
      ...model.rows.map((row) => row.slug),
      ...model.others.map((other) => other.key),
    ];
    const rank = new Map(order.map((key, index) => [key, index]));
    const owners = slices
      .flatMap((slice) => slice.series.owners)
      .sort((a, b) => (rank.get(a.key) ?? 999) - (rank.get(b.key) ?? 999));
    const merged = mergeOwnerRows(
      slices.map((slice) => slice.series.total),
      owners,
    );
    // The merge reports the keys it actually drew; the order above is what
    // ranked them, so a key with no series at all simply has no line.
    return merged;
  }, [slices, model.rows, model.others]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {groups.map(({ server, targets }) => (
        <ServerFloor
          key={server}
          server={server}
          targets={targets}
          names={named}
          onSlice={report}
        />
      ))}

      <FloorStrip model={model} servers={groups.length} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl space-y-4 p-4">
          <FloorChart
            model={model}
            rows={chart.rows}
            keys={chart.keys}
            params={params}
            setParams={setParams}
          />
          <FloorBreakdowns model={model} />
          <FloorRows model={model} rows={chart.rows} />
        </div>
      </div>
    </div>
  );
}

/**
 * One server's fleet, folded and charted for the agents that trade on it.
 *
 * Renders nothing, exactly as `ServerFold` renders nothing: a hook cannot be
 * called in a loop and a fleet is fetched per server, so each server gets a
 * component of its own that owns the fetch and reports what it folded. Grouping
 * the rows by server on screen would reorder the page around a fact the reader
 * did not ask about.
 *
 * `history: true` — and that is the real cost of this page: a paged walk per
 * controller per server (CORR-237 established that a collapsed multi-controller
 * request silently drops controllers at any interval coarser than 5m). Under
 * `useFleetData`'s own query keys, so a reader who also has `/bots` open on this
 * server shares the caches rather than doubling them.
 */
function ServerFloor({
  server,
  targets,
  names,
  onSlice,
}: {
  server: string;
  targets: readonly FoldTarget[];
  names: readonly { slug: string; name: string }[];
  onSlice: (server: string, slice: FloorSlice) => void;
}) {
  const fleet = useFleetData(server, { population: "running", history: true });
  const now = useSeconds(false);

  // One converter with `/bots`, the Money view and the fleet overview's row:
  // the fold converts per leaf using the leaf's own quote, and two numbers that
  // differed by an FX fallback would be exactly the disagreement ARCH-324
  // closed.
  const cv = useMemo(() => quoteConverter(fleet.convert), [fleet.convert]);

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

  const rows = useMemo(
    () =>
      foldRows(targets, {
        leaves,
        deeds: fleet.deeds,
        convert: cv,
        now,
        symbol: fleet.currencySymbol ?? "$",
      }),
    [targets, leaves, fleet.deeds, cv, now, fleet.currencySymbol],
  );

  const partition = useMemo(
    () =>
      partitionFloor({
        leaves,
        deeds: fleet.deeds,
        convert: cv,
        now,
        listed: targets.map((target) => target.slug),
      }),
    [leaves, fleet.deeds, cv, now, targets],
  );

  const nameOf = useMemo(
    () => new Map(names.map((entry) => [entry.slug, entry.name])),
    [names],
  );

  const series = useMemo(() => {
    const owners: SeriesOwner[] = [
      ...[...rows].map(([slug, fold]) => ({
        key: slug,
        label: nameOf.get(slug) ?? slug,
        keys: fold.keys,
      })),
      ...partition.others.map((other) => ({
        key: other.key,
        label: other.label,
        keys: other.keys,
      })),
    ];
    return ownerSeries(fleet.snapshots, owners, fleet.controllers, fleet.convert);
  }, [rows, partition.others, nameOf, fleet.snapshots, fleet.controllers, fleet.convert]);

  const slice = useMemo<FloorSlice>(
    () => ({
      server,
      symbol: fleet.currencySymbol ?? "$",
      rows,
      others: partition.others,
      total: partition.total,
      byPair: groupSpine(partition.spine, (leaf) => leaf.pair || UNKNOWN_LABEL, cv, now),
      byVenue: groupSpine(
        partition.spine,
        (leaf) => leaf.connector || UNKNOWN_LABEL,
        cv,
        now,
      ),
      series,
    }),
    [server, fleet.currencySymbol, rows, partition, cv, now, series],
  );

  useEffect(() => onSlice(server, slice), [server, slice, onSlice]);

  return null;
}
