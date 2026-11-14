import { useRef } from "react";

import { MAX_SPLIT_FRAC, MIN_SPLIT_FRAC } from "@/hooks/useDockSplit";
import { useResizeDrag } from "@/hooks/useResizeDrag";

/**
 * The seam *between* two open dock sections, and the way to move it.
 *
 * `DockResizeHandle` one module over moves the edge between a dock and the
 * conversation — how wide the column is. This one moves the boundary inside it:
 * how much of that column each section gets.
 *
 * The sections opened at a fixed even split, which is the right default and was
 * the only thing on offer: a reader who wanted the execution table tall had to
 * collapse the portfolio away entirely, and "put that away" is a different
 * request from "give me more room for this". So the boundary is dragged, and
 * remembered per browser under one key (see `DESK_SPLIT_KEY`).
 *
 * Where the seam *is* is `useDockSplit`'s, a hook away, because the shares
 * belong to whoever draws the sections — this reports where the pointer went
 * and nothing else.
 */

/** Header plus a couple of rows — under this a section says nothing at all. */
const MIN_SECTION_PX = 96;

/**
 * Drag it, or step it with the arrow keys; double-click puts it back.
 *
 * Rendered as the sibling between the two sections, so the frame it measures is
 * the two elements on either side of it — taken once, at `mousedown`, because a
 * table that grows a row mid-drag must not move the frame the pointer is being
 * read against. The clamp is in pixels for the same reason the pane's is: a
 * floor in percent means something different in a short panel than a tall one,
 * and what matters is that neither section falls below a header and a row.
 */
export function DockSplitHandle({
  frac,
  setFrac,
  defaultFrac,
  label,
  axis = "y",
}: {
  frac: number;
  setFrac: (f: number) => void;
  /** Where a double-click puts it back to. */
  defaultFrac: number;
  /** What this seam separates, for the reader who cannot see it. */
  label: string;
  /**
   * `y` (default): the sections are one above the other and the seam is a
   * horizontal line. `x`: they sit side by side and the seam is vertical.
   */
  axis?: "x" | "y";
}) {
  const geom = useRef({ top: 0, avail: 1 });
  const across = axis === "x";

  const { onMouseDown: startDrag, isDragging } = useResizeDrag({
    axis,
    value: 0, // `compute` is absolute; the drag has no starting size to grow.
    onChange: (px) => setFrac(px / geom.current.avail),
    min: MIN_SECTION_PX,
    max: () => geom.current.avail - MIN_SECTION_PX,
    compute: (coord) => coord - geom.current.top,
    cursor: across ? "col-resize" : "row-resize",
    lockUserSelect: true,
  });

  const onMouseDown = (e: React.MouseEvent) => {
    const handle = e.currentTarget;
    const above = handle.previousElementSibling as HTMLElement | null;
    const below = handle.nextElementSibling as HTMLElement | null;
    if (above && below) {
      const r = above.getBoundingClientRect();
      geom.current = across
        ? { top: r.left, avail: above.offsetWidth + below.offsetWidth }
        : { top: r.top, avail: above.offsetHeight + below.offsetHeight };
    }
    startDrag(e);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Down grows the section above, up gives the room back — the seam moves the
    // way the arrow points.
    if (e.key === (across ? "ArrowRight" : "ArrowDown")) setFrac(frac + 0.02);
    else if (e.key === (across ? "ArrowLeft" : "ArrowUp")) setFrac(frac - 0.02);
    else return;
    e.preventDefault();
  };

  return (
    <div
      role="separator"
      aria-orientation={across ? "vertical" : "horizontal"}
      aria-label={label}
      aria-valuenow={Math.round(frac * 100)}
      aria-valuemin={Math.round(MIN_SPLIT_FRAC * 100)}
      aria-valuemax={Math.round(MAX_SPLIT_FRAC * 100)}
      tabIndex={0}
      onMouseDown={onMouseDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => setFrac(defaultFrac)}
      title="Drag to resize — double-click to reset"
      // Pulled up over the section border above it, so the grabbable strip is
      // the seam the reader can see rather than a gap below it.
      className={`${across ? "-ml-1 w-1.5 cursor-col-resize" : "-mt-1 h-1.5 cursor-row-resize"} shrink-0 transition-colors hover:bg-[var(--color-primary-fill)]/30 focus:outline-none focus-visible:bg-[var(--color-primary-fill)]/30 ${
        isDragging ? "bg-[var(--color-primary-fill)]/30" : ""
      }`}
    />
  );
}
