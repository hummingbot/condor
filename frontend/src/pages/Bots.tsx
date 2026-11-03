import { Navigate, useSearchParams } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { PerfBrowser } from "@/components/perf/PerfBrowser";
import { FallbackSpinner } from "@/components/ui/FallbackSpinner";
import { useFleetData } from "@/hooks/useFleetData";
import { useServer } from "@/hooks/useServer";
import { parsePopulation } from "@/lib/perf-tree";

/**
 * `/bots` is the controller browser (FEAT-084).
 *
 * The tab bar, the stat-card strip, the sortable controllers table and the bots
 * accordion that used to stand in front of it are gone: the browser's scope
 * sidebar (fleet → bot → controller) *is* the page, and every bot-level action
 * that lived in the accordion is reachable from the scope it belongs to.
 *
 * What is left here is a *host*: the no-data guards, and the browser over
 * `useFleetData` (FEAT-108). The fleet query and the performance-history walk
 * that used to live in this file are in that hook now, unchanged and under the
 * same query keys — so the agent workspace can mount the same browser over the
 * same caches instead of copying two hundred lines of fetching.
 *
 * `?tab=runs` is the one interim exception: the run history is still its own
 * padded table until [[FEAT-086]] folds it into the browser's Terminated
 * population. `?tab=archived` is the retired link Runs absorbed.
 */
export function Bots() {
  const [searchParams] = useSearchParams();
  const population = parsePopulation(searchParams.get("population"));

  const { server } = useServer();
  // `?tab=runs` was the run history's own padded table, and `?tab=archived` the
  // retired link it absorbed. Both are the Terminated population now, so the
  // old links land on the scope that answers them (FEAT-086).
  const tab = searchParams.get("tab");
  const legacyRunsTab = tab === "runs" || tab === "archived";

  const fleet = useFleetData(server, { population });

  if (legacyRunsTab) {
    return <Navigate to="/bots?population=terminated" replace />;
  }

  if (!server) {
    return (
      <div className="p-6">
        <NoServerCard message="Select a server from the sidebar to view active bots." />
      </div>
    );
  }
  if (fleet.isLoading) return <FallbackSpinner />;
  if (fleet.error)
    return (
      <p className="p-6 text-[var(--color-red)]">
        {fleet.error instanceof Error ? fleet.error.message : "Error"}
      </p>
    );

  if (!fleet.serverOnline) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-[var(--color-yellow)]/40 bg-[var(--color-yellow)]/10 px-4 py-3">
          <p className="text-sm font-medium text-[var(--color-yellow)]">
            Unable to reach server
          </p>
          {fleet.errorHint && (
            <p className="text-xs text-[var(--color-text-muted)] mt-1">{fleet.errorHint}</p>
          )}
        </div>
      </div>
    );
  }

  // Nothing is said here about an empty fleet or a silent bot: both are the
  // browser's sentences now, drawn in its report pane where the records they are
  // about would be (CORR-356). The page owning them is what made an empty live
  // fleet a page *instead of* the browser, which stranded the whole terminated
  // drill-in behind a hand-edited URL (CORR-357).
  return (
    <PerfBrowser
      controllers={fleet.controllers}
      bots={fleet.bots}
      server={server}
      convert={fleet.convert}
      currencySymbol={fleet.currencySymbol}
      // The fleet history the hook walked: the browser's combined scopes fold
      // these rows rather than issuing a second walk of their own.
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
  );
}
