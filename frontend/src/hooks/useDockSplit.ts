import { useCallback, useEffect, useState } from "react";

/**
 * How a dock's two open sections divide the column, remembered per browser.
 *
 * Kept as a *fraction* of the panel rather than a height, for the reason the
 * workspace pane's split is: the panel's own height changes with the window,
 * and a stored measurement would leave the seam where a taller window put it.
 *
 * The state lives with whoever *draws* the sections, because the shares are
 * theirs to apply — `DockSplitHandle` only reports where the pointer went.
 */

/** The envelope the stored fraction may take, whatever the panel's height. */
export const MIN_SPLIT_FRAC = 0.15;
export const MAX_SPLIT_FRAC = 0.85;

function clampFrac(f: number, fallback: number): number {
  if (!Number.isFinite(f)) return fallback;
  return Math.max(MIN_SPLIT_FRAC, Math.min(MAX_SPLIT_FRAC, f));
}

export function useDockSplit(key: string, fallback = 0.5) {
  const [frac, setFracState] = useState(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored === null
        ? fallback
        : clampFrac(parseFloat(stored), fallback);
    } catch {
      // Unreadable storage is a browser that has never dragged the seam.
      return fallback;
    }
  });

  const setFrac = useCallback(
    (f: number) => setFracState(clampFrac(f, fallback)),
    [fallback],
  );

  useEffect(() => {
    try {
      localStorage.setItem(key, String(frac));
    } catch {
      /* private mode; the split just lasts the session */
    }
  }, [key, frac]);

  return { frac, setFrac, defaultFrac: fallback };
}
