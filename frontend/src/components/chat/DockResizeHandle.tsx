import { useResizeDrag } from "@/hooks/useResizeDrag";

/**
 * The seam between the conversation and a dock, and the way to move it.
 *
 * Several columns of chrome share one window, and how much of it any one of
 * them is worth depends on what the reader is doing: watching a routine's rows
 * wants room, reading an executor table wants more, typing wants the
 * transcript. So it is theirs to set, and remembered by whoever holds the
 * width — per-browser, like the collapse beside it.
 *
 * Dragging left grows the dock, which is the direction a right-anchored panel's
 * edge moves; the arrows do the same, so the handle is reachable without a
 * pointer. Double-click puts it back.
 *
 * Shared by both docks (FEAT-094): the context dock, whose subject is this
 * conversation, and the account dock, whose subject is the server it trades on.
 * It was private to `ContextDock` while there was one resizable column; a
 * second one copying forty lines of drag, clamp and keyboard handling is how
 * two seams start behaving differently under the same gesture.
 */
export function DockResizeHandle({
  width,
  onWidth,
  min,
  max,
  reset,
  label,
}: {
  width: number;
  onWidth: (next: number) => void;
  min: number;
  /** The ceiling, read at drag time — it depends on the window's width. */
  max: () => number;
  /** Where a double-click puts it back to. */
  reset: number;
  /** What this seam separates, for the reader who cannot see it. */
  label: string;
}) {
  const { onMouseDown, isDragging } = useResizeDrag({
    axis: "x",
    value: width,
    onChange: onWidth,
    min,
    max,
    direction: "inverted",
    cursor: "col-resize",
    lockUserSelect: true,
  });

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft") onWidth(Math.min(max(), width + 16));
    else if (e.key === "ArrowRight") onWidth(Math.max(min, width - 16));
    else return;
    e.preventDefault();
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(width)}
      aria-valuemin={min}
      tabIndex={0}
      onMouseDown={onMouseDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onWidth(reset)}
      title="Drag to resize — double-click to reset"
      className={`-ml-1 w-1.5 shrink-0 cursor-col-resize transition-colors hover:bg-[var(--color-primary)]/30 focus:outline-none focus-visible:bg-[var(--color-primary)]/30 ${
        isDragging ? "bg-[var(--color-primary)]/30" : ""
      }`}
    />
  );
}
