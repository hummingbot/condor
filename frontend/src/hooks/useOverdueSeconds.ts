import { useCallback, useSyncExternalStore } from "react";

/**
 * How many whole seconds a running loop's next tick is overdue, or -1 while it
 * is on time — a clock that only re-renders when that number moves.
 *
 * The run screen needs one per-second reading from its loop: the overdue alert,
 * which counts up once a tick is late. It used to take it from
 * {@link useSeconds} at the screen's root, so a healthy loop re-rendered every
 * open band once a second to print nothing (PERF-372). Here the interval still
 * runs while the loop does, but the snapshot is the derived integer rather than
 * the time, and `useSyncExternalStore` compares snapshots with `Object.is`: an
 * on-time loop yields -1 every second and renders nothing, a late one yields a
 * new integer exactly when the alert's text has to change.
 *
 * The countdown beside it (`LoopBar`) keeps `useSeconds`: it is a leaf, and it
 * re-renders only itself.
 */
export function useOverdueSeconds(
  loop: {
    status: string;
    last_tick_at: number;
    frequency_sec: number;
  } | null,
): number {
  const due =
    loop &&
    loop.status === "running" &&
    loop.last_tick_at > 0 &&
    loop.frequency_sec > 0
      ? loop.last_tick_at + loop.frequency_sec
      : 0;

  const subscribe = useCallback(
    (onChange: () => void) => {
      if (due <= 0) return () => {};
      const id = setInterval(onChange, 1000);
      return () => clearInterval(id);
    },
    [due],
  );
  const snapshot = useCallback(() => overdueAt(due, Date.now()), [due]);

  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Whole seconds past `dueSec` at `nowMs`, or -1 when not past it (or nothing is due). */
export function overdueAt(dueSec: number, nowMs: number): number {
  if (dueSec <= 0) return -1;
  const late = nowMs / 1000 - dueSec;
  return late > 0 ? Math.floor(late) : -1;
}
