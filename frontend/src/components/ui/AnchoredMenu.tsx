import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useDismissOnOutsideClick } from "@/hooks/useDismissOnOutsideClick";
import { useEscapeKey } from "@/hooks/useEscapeKey";

interface Props {
  /** The button the panel hangs off. */
  anchor: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  /** Which edge of the panel lines up with the same edge of the anchor. */
  align?: "left" | "right";
  /**
   * Make the panel at least as wide as its trigger. A select's menu has to line
   * up with its field, and `w-full` cannot say that once the panel is portalled
   * — 100% would be of `document.body`.
   */
  matchAnchorWidth?: boolean;
  /**
   * Distance between the trigger and the panel, in px. Defaults to a floating
   * 6; a menu drawn as a continuation of its trigger passes ~1.
   */
  gap?: number;
  /** Width utility for the panel, e.g. `w-44`. */
  className?: string;
  role?: React.AriaRole;
  children: React.ReactNode;
}

/** Default distance between trigger and panel, and between panel and viewport edge. */
const DEFAULT_GAP = 6;
const EDGE = 8;
/**
 * Below this much room the panel would open as a useless sliver, so it is worth
 * flipping above the trigger instead — the case that matters is the last field
 * in a config panel, which sits just above the launch footer.
 */
const MIN_ROOM = 160;

/**
 * A dropdown panel that no ancestor can clip.
 *
 * An `absolute` menu is still a child of its trigger, so any `overflow-hidden`
 * on the way up cuts it off. That is exactly what happened to the DEX chain and
 * venue pickers: the browser wraps its header and pool table in one clipping
 * card, and on a chain with no pools that card is barely taller than the header
 * itself — so most of the chain list was invisible and unclickable, on the very
 * screen where switching chains is the only thing left to do.
 *
 * A scrolling ancestor fails differently and just as badly. The executor and LP
 * config forms live in a `flex-1 overflow-y-auto` panel: an absolute menu there
 * does extend the scroller's scrollHeight, so the options are reachable — but
 * nothing scrolls for you, so a user already at the bottom of the panel opens
 * the select and sees a few pixels of list and no cue that more scroll room
 * just appeared.
 *
 * Rendering into `document.body` at fixed coordinates escapes every clipping
 * and scrolling ancestor. The trade is that the panel no longer moves with the
 * page, so it is re-anchored on scroll and resize instead.
 */
export function AnchoredMenu({
  anchor,
  open,
  onClose,
  align = "left",
  matchAnchorWidth = false,
  gap = DEFAULT_GAP,
  className = "",
  role,
  children,
}: Props) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{
    top?: number;
    bottom?: number;
    left?: number;
    right?: number;
    maxHeight: number;
    minWidth?: number;
  } | null>(null);

  // Stale coordinates from a previous open are harmless: the panel renders
  // nothing while closed, and this runs before the browser paints the reopen.
  useLayoutEffect(() => {
    if (!open || !anchor) return;
    const place = () => {
      const r = anchor.getBoundingClientRect();
      const below = window.innerHeight - r.bottom - gap - EDGE;
      const above = r.top - gap - EDGE;
      // Open upward only when downward is cramped *and* upward is roomier —
      // otherwise the familiar direction wins.
      const vertical =
        below < Math.min(MIN_ROOM, above)
          ? { bottom: window.innerHeight - r.top + gap, maxHeight: Math.max(above, 0) }
          : { top: r.bottom + gap, maxHeight: Math.max(below, 0) };
      setPos({
        ...vertical,
        ...(align === "right"
          ? { right: Math.max(EDGE, window.innerWidth - r.right) }
          : { left: Math.max(EDGE, r.left) }),
        minWidth: matchAnchorWidth ? r.width : undefined,
      });
    };
    place();
    // Capture phase: the page's own scroll containers do not bubble scroll.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, anchor, align, matchAnchorWidth, gap]);

  useDismissOnOutsideClick(open, onClose, [menuRef, anchor]);
  useEscapeKey(open, onClose);

  if (!open || !pos) return null;

  return createPortal(
    <div
      ref={menuRef}
      role={role}
      style={{
        position: "fixed",
        top: pos.top,
        bottom: pos.bottom,
        left: pos.left,
        right: pos.right,
        // Never taller than the room on the side it opened to, so a long list
        // scrolls inside the panel rather than running off the window.
        maxHeight: pos.maxHeight,
        minWidth: pos.minWidth,
      }}
      className={`z-50 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] shadow-xl ${className}`}
    >
      {children}
    </div>,
    document.body,
  );
}
