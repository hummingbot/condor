import { useEffect, useState } from "react";

/**
 * A second-resolution clock, alive only while there is a countdown to run.
 *
 * The browser's own clock ticks once a minute (every per-hour pace is derived
 * from it, and a `Date.now()` read during render is what makes
 * `useSyncExternalStore` re-render forever). A "next tick in 38s" quantised to
 * that would sit on 38 for a minute and then jump, which is worse than not
 * showing it — so the places that need seconds keep their own interval, and
 * stop it the moment the loop is not running.
 *
 * It lived inside `AgentScopeHeader` while the fleet band was the only surface
 * that counted down to a tick. `LoopPulse` is the second, and a clock copied is
 * a clock that drifts: two intervals started a frame apart show the same loop
 * one second out from itself on the same screen.
 */
export function useSeconds(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const tick = () => setNow(Date.now());
    // The first read is scheduled rather than taken here: a loop that starts
    // long after this mounted would otherwise show one frame of a countdown
    // measured from mount time, and setting state in an effect body is what
    // the render-phase rule forbids anyway.
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, 1000);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [active]);
  return now;
}
