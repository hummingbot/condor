/**
 * A hint that teaches one keyboard shortcut, once.
 *
 * A key spelled on its control as a bare `<kbd>/</kbd>` names the key without
 * saying what it does — QA read the market chip's and reported "i saw the /,
 * dunno what it was for until I pressed it". A sentence says it; a glyph does
 * not. So the sentence is shown the first time someone rests on the control,
 * the fact that it has been taught is remembered, and from then on the hint
 * gets out of the way for good and the control's `title` is the only permanent
 * affordance left.
 *
 * ── Why it is a device preference ──
 *
 * The "taught" flag records how far this browser has been onboarded. It says
 * nothing about what the user was doing, so it is not session state and is not
 * cleared at a session boundary (lib/sessionState.ts states that rule and
 * enumerates the keys kept under it).
 */

import { useCallback, useEffect, useRef, useState } from "react";

function wasTaught(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    // Storage disabled: show the hint every time rather than never. It is one
    // line of muted text on hover, which is the cheaper of the two failures.
    return false;
  }
}

function remember(key: string) {
  try {
    localStorage.setItem(key, "1");
  } catch {
    /* nothing to remember it in; the hint just shows again next time */
  }
}

export interface OneTimeHint {
  /** True while the bubble should be on screen. */
  visible: boolean;
  /**
   * True until the hint has been taught.
   *
   * The anchor drops its `title` while this holds, so the browser's own tooltip
   * and this bubble never stack on the same hover — and the `title` comes back
   * as the durable reminder the moment the hint retires.
   */
  pending: boolean;
  /** Spread onto the control the hint is anchored to. */
  hoverProps: {
    onPointerEnter: () => void;
    onPointerLeave: () => void;
  };
  /** Retire the hint unshown — the shortcut was just used, so it is known. */
  markTaught: () => void;
}

export function useOneTimeHint(
  storageKey: string,
  { delayMs = 400, holdMs = 4000 }: { delayMs?: number; holdMs?: number } = {},
): OneTimeHint {
  const [pending, setPending] = useState(() => !wasTaught(storageKey));
  const [visible, setVisible] = useState(false);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Whether this hover actually got as far as putting the bubble up. */
  const shownRef = useRef(false);

  const clearTimers = useCallback(() => {
    if (showTimer.current !== null) {
      clearTimeout(showTimer.current);
      showTimer.current = null;
    }
    if (hideTimer.current !== null) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const markTaught = useCallback(() => {
    clearTimers();
    shownRef.current = false;
    setVisible(false);
    setPending(false);
    remember(storageKey);
  }, [clearTimers, storageKey]);

  const onPointerEnter = useCallback(() => {
    if (!pending) return;
    clearTimers();
    showTimer.current = setTimeout(() => {
      showTimer.current = null;
      shownRef.current = true;
      setVisible(true);
      // Written on the way up, not on the way out: a tab closed while the
      // bubble is still on screen has been taught all the same, and re-teaching
      // it on the next visit is exactly the noise this is meant to avoid.
      remember(storageKey);
      hideTimer.current = setTimeout(() => {
        hideTimer.current = null;
        setVisible(false);
        setPending(false);
      }, holdMs);
    }, delayMs);
  }, [pending, clearTimers, delayMs, holdMs, storageKey]);

  const onPointerLeave = useCallback(() => {
    clearTimers();
    setVisible(false);
    // Only a hover that reached the bubble retires the hint. Sweeping the
    // pointer across the control on the way somewhere else must not burn it.
    if (shownRef.current) {
      shownRef.current = false;
      setPending(false);
    }
  }, [clearTimers]);

  return {
    visible,
    pending,
    hoverProps: { onPointerEnter, onPointerLeave },
    markTaught,
  };
}
