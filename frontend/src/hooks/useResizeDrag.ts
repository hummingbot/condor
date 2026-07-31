import { useCallback, useEffect, useRef, useState } from "react";

type Axis = "x" | "y";

interface UseResizeDragOptions {
  /** Which pointer axis drives the resize. */
  axis: Axis;
  /** Current size, used as the drag's starting point. */
  value: number;
  /** Apply the clamped, computed size. */
  onChange: (next: number) => void;
  /** Minimum allowed size (clamp lower bound). Default 0. */
  min?: number;
  /**
   * Maximum allowed size (clamp upper bound). May be a function so it can be
   * recomputed each move (e.g. relative to `window.innerWidth`). Default Infinity.
   */
  max?: number | (() => number);
  /**
   * Sign of the delta. With "normal", moving the pointer in the positive axis
   * direction grows the size; with "inverted" it shrinks it (panels anchored to
   * the right/bottom). Default "normal".
   */
  direction?: "normal" | "inverted";
  /**
   * Override the size computation entirely. Receives the pointer coordinate on
   * `axis` for the active move and the size captured at drag start, and returns
   * the raw (pre-clamp) next size. Useful for absolute positioning (e.g.
   * `window.innerWidth - clientX`). When provided, `direction` is ignored.
   */
  compute?: (coord: number, startValue: number) => number;
  /** Cursor applied to `document.body` while dragging (e.g. "col-resize"). */
  cursor?: string;
  /** Disable text selection on `document.body` while dragging. Default false. */
  lockUserSelect?: boolean;
}

/**
 * Pointer-drag resize handle. Wires `mousemove`/`mouseup` on `document` for the
 * duration of a drag, clamps the resulting size, and manages body cursor /
 * user-select. Listeners and body styles are always torn down on mouseup and on
 * unmount mid-drag, so an interrupted drag never leaks a document listener nor a
 * stuck cursor/selection (see CORR-008).
 *
 * Returns `{ onMouseDown, isDragging }`: spread `onMouseDown` onto the handle
 * element; `isDragging` drives optional UI state.
 */
export function useResizeDrag(options: UseResizeDragOptions) {
  const optsRef = useRef(options);
  useEffect(() => {
    optsRef.current = options;
  });

  const [isDragging, setIsDragging] = useState(false);
  // Holds the teardown for an in-flight drag so an unmount mid-drag still
  // detaches listeners and restores body cursor/userSelect.
  const dragCleanup = useRef<(() => void) | null>(null);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const { axis, value, onChange, min = 0, max = Infinity, direction = "normal", compute, cursor, lockUserSelect } =
      optsRef.current;

    const startCoord = axis === "x" ? e.clientX : e.clientY;
    const startValue = value;

    setIsDragging(true);
    if (cursor) document.body.style.cursor = cursor;
    if (lockUserSelect) document.body.style.userSelect = "none";

    const onMove = (ev: MouseEvent) => {
      const coord = axis === "x" ? ev.clientX : ev.clientY;
      const raw = compute
        ? compute(coord, startValue)
        : direction === "inverted"
          ? startValue + (startCoord - coord)
          : startValue + (coord - startCoord);
      const upper = typeof max === "function" ? max() : max;
      onChange(Math.max(min, Math.min(upper, raw)));
    };

    const cleanup = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", cleanup);
      if (cursor) document.body.style.cursor = "";
      if (lockUserSelect) document.body.style.userSelect = "";
      dragCleanup.current = null;
      setIsDragging(false);
    };

    dragCleanup.current = cleanup;
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", cleanup);
  }, []);

  // Tear down a drag interrupted by unmount.
  useEffect(() => () => dragCleanup.current?.(), []);

  return { onMouseDown, isDragging };
}
