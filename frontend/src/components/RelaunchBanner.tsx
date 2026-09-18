import { RotateCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useRelaunch } from "@/hooks/useRelaunch";
import { updatesApi } from "@/lib/updates-api";

/**
 * "You updated; apply it" — and then it applies it.
 *
 * This used to say "run `make restart`", on the reasoning that re-execing races
 * whatever started Condor into a second copy on the same port. That is not what
 * the code does. `request_restart()` raises SIGTERM rather than exec'ing, so
 * `teardown()` runs and `main()` execs only once the loop is gone; the exec then
 * replaces the process image in place, keeping the pid, the parent and the
 * controlling terminal. Nothing exits, so there is no second copy, and the
 * listening socket is non-inheritable (PEP 446) and released by the exec.
 * Telegram has had a button for this all along.
 *
 * So the browser does what a router does when its firmware updates: counts
 * down, restarts, and comes back on its own. The countdown is cancellable
 * because two admins may be watching and one of them may want to pick the
 * moment.
 *
 * The reload at the end is not optional. The build swaps in a new bundle with
 * new hashed asset names, so a browser holding the old index.html would be
 * running a different build from the API answering it.
 */

/** Long enough to read the sentence and hit Cancel; short enough not to nag. */
const COUNTDOWN_SECONDS = 5;

/** How hard to try before admitting the server is not coming back. */
const RECONNECT_TIMEOUT_MS = 90_000;
const RECONNECT_INTERVAL_MS = 1_000;

type Phase = "idle" | "counting" | "restarting" | "cancelled" | "stuck";

export function RelaunchBanner() {
  const { data } = useRelaunch();
  const [phase, setPhase] = useState<Phase>("idle");
  const [left, setLeft] = useState(COUNTDOWN_SECONDS);
  const [showHow, setShowHow] = useState(false);
  const started = useRef(false);

  const required = data?.required ?? false;

  // Wait for the server to answer again, then reload once to pick up the new
  // bundle. Polled rather than pushed: the socket is down for the whole window,
  // and this needs to work before any of the app's own channels are back.
  const waitForServer = useCallback(() => {
    const deadline = Date.now() + RECONNECT_TIMEOUT_MS;
    const tick = async () => {
      try {
        const res = await fetch("/api/v1/meta/relaunch", { cache: "no-store" });
        if (res.ok) {
          window.location.reload();
          return;
        }
      } catch {
        // Expected for most of the window — the server is not listening yet.
      }
      if (Date.now() > deadline) {
        setPhase("stuck");
        return;
      }
      window.setTimeout(tick, RECONNECT_INTERVAL_MS);
    };
    window.setTimeout(tick, RECONNECT_INTERVAL_MS);
  }, []);

  const relaunchNow = useCallback(async () => {
    setPhase("restarting");
    try {
      await updatesApi.relaunch();
    } catch {
      // The request is *expected* not to answer: the process handling it tears
      // down and execs, so the socket usually closes first. A failure here says
      // nothing about whether the restart is happening — the reconnect decides.
    }
    waitForServer();
  }, [waitForServer]);

  // Start the countdown once, the first time a relaunch becomes required.
  useEffect(() => {
    if (!required || started.current) return;
    started.current = true;
    setPhase("counting");
  }, [required]);

  useEffect(() => {
    if (phase !== "counting") return;
    if (left <= 0) {
      void relaunchNow();
      return;
    }
    const id = window.setTimeout(() => setLeft((n) => n - 1), 1000);
    return () => window.clearTimeout(id);
  }, [phase, left, relaunchNow]);

  if (!required) return null;

  const moved =
    data?.from_commit && data?.target_commit
      ? `${data.from_commit} → ${data.target_commit}`
      : (data?.target_commit ?? "");

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-yellow)]/40 bg-[var(--color-yellow)]/10 px-4 py-2">
      <RotateCw
        className={`h-4 w-4 shrink-0 text-[var(--color-yellow)] ${
          phase === "restarting" ? "animate-spin" : ""
        }`}
      />

      <p className="min-w-0 flex-1 text-xs leading-relaxed text-[var(--color-text)]">
        {phase === "counting" && (
          <>
            <strong className="font-semibold">Condor has been updated.</strong>{" "}
            <span className="text-[var(--color-text-muted)]">
              Restarting in {left}s to apply it
              {moved ? (
                <>
                  {" "}
                  (<span className="font-mono">{moved}</span>)
                </>
              ) : null}
              . Bots and open positions are untouched.
            </span>
          </>
        )}
        {phase === "restarting" && (
          <>
            <strong className="font-semibold">Restarting Condor…</strong>{" "}
            <span className="text-[var(--color-text-muted)]">
              This page will reconnect and reload on its own.
            </span>
          </>
        )}
        {phase === "stuck" && (
          <>
            <strong className="font-semibold">Condor has not come back.</strong>{" "}
            <span className="text-[var(--color-text-muted)]">
              Check the terminal it was started from — if the restart failed, the
              reason is in <span className="font-mono">.condor/restart-failure.log</span>.
            </span>
          </>
        )}
        {phase === "cancelled" && (
          <>
            <strong className="font-semibold">Condor has been updated.</strong>{" "}
            <span className="text-[var(--color-text-muted)]">
              It is still running the code it booted with
              {moved ? (
                <>
                  {" "}
                  (<span className="font-mono">{moved}</span>)
                </>
              ) : null}
              .
            </span>
          </>
        )}
      </p>

      {phase === "counting" && (
        <button
          onClick={() => setPhase("cancelled")}
          className="shrink-0 whitespace-nowrap rounded-md border border-[var(--color-border)] px-3 py-1 text-xs font-medium text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          Cancel
        </button>
      )}

      {(phase === "cancelled" || phase === "stuck") && (
        <button
          onClick={() => void relaunchNow()}
          className="shrink-0 whitespace-nowrap rounded-md bg-[var(--color-yellow)]/20 px-3 py-1 text-xs font-medium text-[var(--color-text)] transition-colors hover:bg-[var(--color-yellow)]/30"
        >
          Restart now
        </button>
      )}

      {phase === "cancelled" && (
        <button
          onClick={() => setShowHow((v) => !v)}
          className="shrink-0 whitespace-nowrap rounded-md px-3 py-1 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          {showHow ? "Hide" : "How?"}
        </button>
      )}

      {showHow && phase === "cancelled" && (
        <p className="w-full text-xs leading-relaxed text-[var(--color-text-muted)]">
          Restart now restarts Condor in place. You can also run{" "}
          <code className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 font-mono text-[11px] text-[var(--color-text)]">
            make restart
          </code>{" "}
          from the Condor directory. Bots and open positions are untouched;
          continuous routines and agent loops are restored on the way back up.
        </p>
      )}
    </div>
  );
}
