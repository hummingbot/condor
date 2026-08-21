import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface Props {
  /** The button the panel hangs off. */
  anchor: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  /** Which edge of the panel lines up with the same edge of the anchor. */
  align?: "left" | "right";
  /** Width utility for the panel, e.g. `w-44`. */
  className?: string;
  children: React.ReactNode;
}

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
 * Rendering into `document.body` at fixed coordinates escapes every clipping
 * ancestor. The trade is that the panel no longer moves with the page, so it is
 * re-anchored on scroll and resize instead.
 */
export function AnchoredMenu({
  anchor,
  open,
  onClose,
  align = "left",
  className = "",
  children,
}: Props) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{
    top: number;
    left?: number;
    right?: number;
  } | null>(null);

  // Stale coordinates from a previous open are harmless: the panel renders
  // nothing while closed, and this runs before the browser paints the reopen.
  useLayoutEffect(() => {
    if (!open || !anchor) return;
    const place = () => {
      const r = anchor.getBoundingClientRect();
      setPos(
        align === "right"
          ? { top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) }
          : { top: r.bottom + 6, left: Math.max(8, r.left) },
      );
    };
    place();
    // Capture phase: the page's own scroll containers do not bubble scroll.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, anchor, align]);

  useEffect(() => {
    if (!open) return;
    // The panel is no longer inside the trigger's subtree, so "outside" has to be
    // measured against both. Miss the panel and a mousedown on a menu item would
    // unmount it before the click that selects it ever lands.
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || anchor?.contains(target)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, anchor, onClose]);

  if (!open || !pos) return null;

  return createPortal(
    <div
      ref={menuRef}
      style={{
        position: "fixed",
        top: pos.top,
        left: pos.left,
        right: pos.right,
        // Never taller than the room below the button, so a long list scrolls
        // inside the panel rather than running off the bottom of the window.
        maxHeight: `calc(100vh - ${pos.top + 8}px)`,
      }}
      className={`z-50 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-1 shadow-xl ${className}`}
    >
      {children}
    </div>,
    document.body,
  );
}
