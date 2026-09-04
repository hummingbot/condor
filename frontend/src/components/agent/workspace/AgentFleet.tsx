import { Layers, Server } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { PerfBrowser } from "@/components/perf/PerfBrowser";
import { useFleetData } from "@/hooks/useFleetData";
import { useServer } from "@/hooks/useServer";
import { attributionOf, runKeyLabel } from "@/lib/agent-attribution";
import type { AgentRunRow } from "@/lib/api";
import { parsePopulation } from "@/lib/perf-tree";

/**
 * Which query parameter the workspace's fleet scope lives in.
 *
 * Not `?scope=`: the workspace's grammar (FEAT-103) already spends `?strategy=`,
 * `?run=` and `?tick=` on the loop, and one more page-level word for a
 * different meaning is how two grammars start overwriting each other. `/bots`
 * keeps `?scope=` untouched, so every link and bookmark pointing at it still
 * resolves with no redirect.
 */
const FSCOPE = "fscope";

/**
 * What this agent is actually running, and what it has made (FEAT-108).
 *
 * The fleet browser, rooted at the agent — the same scope tree, the same
 * filters, the same folds and the same money as `/bots`, with `agent:{runKey}`
 * as its **floor**. This view used to be a deployment ledger and a link out to
 * `/bots`, which is one of the six navigations the workspace exists to delete:
 * following it unmounted the frame, the loop bar and the tick spine, and Back
 * did not come home.
 *
 * Nothing is rebuilt here. `PerfBrowser` was already host-agnostic — every
 * record arrives as a prop — and `useFleetData` (FEAT-108) is `/bots`'s
 * fetching lifted out of the page, under the same query keys, so this host and
 * that one share one set of queries through react-query.
 *
 * **Which server.** `/bots` is scoped to the ambient server; the fleet map
 * deliberately is not, because an agent owns its namespace wherever its bots
 * run. So this reads the agent's *own* server — the strategy's configured one,
 * else the agent's pin, else the ambient one — rather than showing an empty
 * fleet for an agent that trades somewhere else. When that is not the server
 * the rest of the app is on, the line above the browser says so and offers to
 * move the app to it.
 */
export function AgentFleet({
  slug,
  sslug,
  serverName,
  run,
}: {
  slug: string;
  sslug: string;
  /** The agent's own server: the strategy's config, else the agent's pin. */
  serverName: string;
  /** The run the loop bar has selected, which is what the fleet is narrowed to. */
  run: AgentRunRow | null;
}) {
  const { server: ambient, setServer } = useServer();
  const [searchParams] = useSearchParams();
  const population = parsePopulation(searchParams.get("population"));

  // The agent's own server wins over the ambient one: an agent whose bots run
  // on `brigado_2` has a fleet, and the ambient server simply is not where it
  // is. Falls back to the ambient server for an agent with no pin at all.
  const server = serverName || ambient;
  const elsewhere = !!serverName && !!ambient && serverName !== ambient;

  const fleet = useFleetData(server, { population });

  const runKey = `${slug}.${sslug}`;
  const rootScope = `agent:${runKey}`;

  /**
   * The run filter, and the one way to turn it off.
   *
   * The loop bar always has a run selected — `pickRun` falls back to the newest
   * in scope — so the run is passed *in* rather than read from `?run=`: a
   * browser reading the URL would see no run exactly when one is selected by
   * default. Clearing it is recorded as *which* run was cleared, so selecting a
   * different run in the loop bar arms the filter again without an effect
   * writing state back.
   */
  const [clearedRun, setClearedRun] = useState<string | null>(null);
  const runNum =
    run && run.kind === "session" && clearedRun !== run.run_id ? run.number : null;
  const clearRun = useCallback(() => setClearedRun(run?.run_id ?? null), [run]);

  /**
   * How much of the fleet this view is leaving out.
   *
   * The one thing the browser cannot say about itself: it is rooted, so its own
   * counts are the root's. Counted over the same records the tree is built from
   * and by the same rule (`attributionOf`), so the two can never disagree about
   * which controllers are this agent's.
   */
  const counts = useMemo(() => {
    const all =
      population === "terminated" ? fleet.terminatedControllers : fleet.controllers;
    let mine = 0;
    for (const ctrl of all) {
      const owned = attributionOf(
        fleet.owners,
        fleet.deeds,
        ctrl.bot_name,
        ctrl.controller_id || ctrl.controller_name,
      );
      if (owned.runKey === runKey) mine += 1;
    }
    return { mine, total: all.length };
  }, [population, fleet.controllers, fleet.terminatedControllers, fleet.owners, fleet.deeds, runKey]);

  if (!server) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        This agent has no server pinned, and no server is selected — so there is no
        fleet to read. Pin one from the header above.
      </p>
    );
  }

  if (fleet.error) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-red)]">
        {fleet.error instanceof Error ? fleet.error.message : "Failed to read the fleet."}
      </p>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-[11px] text-[var(--color-text-muted)]">
        <span className="tabular-nums">
          Showing {counts.mine.toLocaleString()} of {counts.total.toLocaleString()}{" "}
          {counts.total === 1 ? "controller" : "controllers"} — {runKeyLabel(runKey)}
          &apos;s
        </span>
        <Link
          to={`/bots?scope=${encodeURIComponent(rootScope)}`}
          className="inline-flex items-center gap-1 text-[var(--color-text-muted)] underline-offset-2 transition-colors hover:text-[var(--color-primary)] hover:underline"
          title="The same records, beside everything else that is trading"
        >
          <Layers className="h-3 w-3" /> the whole fleet
        </Link>
        {/* An agent whose bots run somewhere else has a fleet — it is just not
            on the server the rest of the app is pointed at. Say which, and
            offer to move, rather than drawing an empty tree. */}
        {elsewhere && (
          <span className="inline-flex items-center gap-1 text-[var(--color-yellow)]">
            <Server className="h-3 w-3" />
            {runKeyLabel(runKey)}&apos;s bots run on{" "}
            <code className="font-mono">{serverName}</code>
            <button
              type="button"
              onClick={() => setServer(serverName)}
              className="rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
            >
              switch
            </button>
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1">
        <PerfBrowser
          rootScope={rootScope}
          param={FSCOPE}
          run={runNum}
          onClearRun={clearRun}
          controllers={fleet.controllers}
          bots={fleet.bots}
          server={server}
          convert={fleet.convert}
          currencySymbol={fleet.currencySymbol}
          snapshots={fleet.snapshots}
          truncated={fleet.truncated}
          executors={fleet.executors}
          paging={fleet.paging}
          runs={fleet.runs}
          terminatedControllers={fleet.terminatedControllers}
          owners={fleet.owners}
          deeds={fleet.deeds}
          rateFormatPnl={fleet.rateFormatPnl}
          rateFormatValue={fleet.rateFormatValue}
          rateFormatDetailed={fleet.rateFormatDetailed}
        />
      </div>
    </div>
  );
}
