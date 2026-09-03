import { Bot, Rocket } from "lucide-react";
import { useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { PerfBrowser } from "@/components/perf/PerfBrowser";
import { DeployBotDialog } from "@/components/bots/DeployBotDialog";
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
 * What is left here is a *host*: the empty states, and the browser over
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
  // Deploy lives in the browser's fleet-scope header — except when there is no
  // fleet to scope, which is exactly when it is needed most (see below).
  const [showDeploy, setShowDeploy] = useState(false);

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

  // Nothing to scope: the browser draws nothing without controllers, and the
  // fleet header that carries Deploy is part of the browser — so the empty
  // state has to carry the one action that gets out of it.
  //
  // Only for the *live* fleet, though. An empty fleet is exactly the state in
  // which the Terminated population is worth reading — the run history, the
  // closed executors and the archive drill-in all live there, and their queries
  // above have already fetched them — so answering "No bots running" for
  // `?population=terminated` strands the reader on the one screen that still
  // has something to say (CORR-297).
  //
  // "No controllers" and "no bots" are not the same thing, and saying the first
  // as the second is how a broker outage reads as an empty fleet. A controller
  // is reported over the server's MQTT broker; the bot list is not (Docker
  // answers that one). So a bot the server can see, reporting no controller,
  // means the reports are not arriving — and that is worth naming here, on the
  // screen where the bot is missing, rather than leaving it to be found in the
  // API's logs.
  const silentBots = fleet.bots;
  if (population === "running" && fleet.controllers.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-[var(--color-text-muted)]">
        <Bot className="h-10 w-10" />
        {silentBots.length === 0 ? (
          <p>No bots running</p>
        ) : (
          <div className="max-w-md space-y-1 text-center">
            <p className="text-[var(--color-yellow)]">
              {silentBots.length === 1
                ? `${silentBots[0].bot_name} is running but reporting no controllers`
                : `${silentBots.length} bots are running but reporting no controllers`}
            </p>
            <p className="text-xs">
              Controller reports reach the API over its MQTT broker. Check that the broker
              is up and that the API is connected to it — on the server,{" "}
              <code className="font-mono">make doctor</code> names it.
            </p>
          </div>
        )}
        <button
          onClick={() => setShowDeploy(true)}
          className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-5 py-2 text-sm font-medium text-white transition-all hover:shadow-lg hover:shadow-[var(--color-primary)]/20"
        >
          <Rocket className="h-4 w-4" />
          Deploy Bot
        </button>
        <DeployBotDialog open={showDeploy} onClose={() => setShowDeploy(false)} server={server} />
      </div>
    );
  }

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
