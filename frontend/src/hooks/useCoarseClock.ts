import { useSyncExternalStore } from "react";

/**
 * A minute-resolution wall clock, shared by everything that measures elapsed
 * time rather than counting down to something.
 *
 * The runtime figure and every per-hour pace derived from it are elapsed time,
 * so they have to advance on their own — read once during render they would sit
 * frozen until a socket frame happened to re-render the view, and a pace whose
 * divisor is stale is wrong rather than merely old.
 *
 * Subscribed to rather than sampled, so the read stays pure. The snapshot is
 * quantised to the tick because `useSyncExternalStore` compares snapshots with
 * `Object.is`: a raw `Date.now()` returns a new value on every call, including
 * the several React makes within one render pass, which it answers by
 * re-rendering forever.
 *
 * A minute is the resolution the readings themselves have — `formatRuntimeHours`
 * prints 0.1h above an hour and whole minutes below it — so a faster clock buys
 * a byte-identical number at the cost of re-folding the population that
 * produced it. The surfaces that genuinely need seconds (a "next tick in 38s"
 * countdown) keep {@link useSeconds}, which quantising to a minute would leave
 * sitting on 38 for a minute and then jump.
 *
 * It lived in `PerfBrowser` while that page was the only fleet fold on screen.
 * The chat dock is the second, and it was reaching for `useSeconds` for want of
 * this — rebuilding its whole tree once a second to print the same minute.
 */

const CLOCK_TICK_MS = 60_000;

function subscribeToClock(onChange: () => void) {
  const id = setInterval(onChange, CLOCK_TICK_MS);
  return () => clearInterval(id);
}

function clockSnapshot() {
  return Math.floor(Date.now() / CLOCK_TICK_MS) * CLOCK_TICK_MS;
}

export function useCoarseClock(): number {
  return useSyncExternalStore(subscribeToClock, clockSnapshot, clockSnapshot);
}
