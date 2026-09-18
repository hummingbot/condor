/**
 * The empty state for a strategy that has run, but whose runs are not in the
 * loaded window (CORR-376).
 *
 * The rail's window pages chats and delegations; a strategy whose sessions are
 * not among the rows loaded (an older server, a hand-edited `limit`) would
 * otherwise read as "has not run yet" beside a Playbook counting its sessions.
 * Shared by the answer stack and the Runs band's body.
 */
export function OutsideWindow({
  onShowOlderRuns,
}: {
  onShowOlderRuns: () => void;
}) {
  return (
    <p className="text-xs text-[var(--color-text-muted)]">
      This strategy&apos;s runs are older than the runs loaded here.{" "}
      <button
        type="button"
        data-show-older-runs
        onClick={onShowOlderRuns}
        className="underline underline-offset-2 transition-colors hover:text-[var(--color-primary)]"
      >
        Load older runs
      </button>
    </p>
  );
}
