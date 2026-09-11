import { RotateCw } from "lucide-react";
import { useState } from "react";

import { useRelaunch } from "@/hooks/useRelaunch";

/**
 * "You updated; the running Condor is still the old one."
 *
 * An update deliberately stops short of exec'ing the server — Condor is rarely
 * the top of its own process tree, and re-execing races whatever started it
 * into a second copy on the same port (`condor/updates/run.py`). What that
 * trade buys is safety; what it costs is a window where the dashboard bundle in
 * the browser is newer than the API answering it. This strip is what makes that
 * window legible instead of just weird, so it rides above every page rather
 * than living in Settings where only the person who ran the update would see it.
 *
 * There is no dismiss. The only thing that clears it is the relaunch, and the
 * poll notices that on its own — a dismissed banner would just be a mismatch
 * nobody is told about any more.
 */
export function RelaunchBanner() {
  const { data } = useRelaunch();
  const [showHow, setShowHow] = useState(false);

  if (!data?.required) return null;

  const moved =
    data.from_commit && data.target_commit
      ? `${data.from_commit} → ${data.target_commit}`
      : (data.target_commit ?? "");

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-yellow)]/40 bg-[var(--color-yellow)]/10 px-4 py-2">
      <RotateCw className="h-4 w-4 shrink-0 text-[var(--color-yellow)]" />

      <p className="min-w-0 flex-1 text-xs leading-relaxed text-[var(--color-text)]">
        <strong className="font-semibold">Condor has been updated.</strong>{" "}
        <span className="text-[var(--color-text-muted)]">
          Relaunch it to apply the changes — it is still running the code it
          booted with
          {moved ? (
            <>
              {" "}
              (<span className="font-mono">{moved}</span>)
            </>
          ) : null}
          .
        </span>
      </p>

      <button
        onClick={() => setShowHow((v) => !v)}
        className="shrink-0 whitespace-nowrap rounded-md px-3 py-1 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
      >
        {showHow ? "Hide" : "How?"}
      </button>

      {showHow && (
        <p className="w-full text-xs leading-relaxed text-[var(--color-text-muted)]">
          Run{" "}
          <code className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 font-mono text-[11px] text-[var(--color-text)]">
            make restart
          </code>{" "}
          from the Condor directory, or stop and start it however you launched
          it. Bots and open positions are untouched; continuous routines and
          agent loops are restored on the way back up.
        </p>
      )}
    </div>
  );
}
