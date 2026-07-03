import { useEffect, useRef } from "react";

/**
 * Close-on-Escape for hand-rolled modal overlays. While `active` is true,
 * listens for `Escape` keydown on `window` and invokes `onClose`, calling
 * `preventDefault()` exactly like the dialogs' backdrop/Cancel paths expect
 * (see CORR-077).
 *
 * The callback is kept in a ref so passing an inline closure never
 * re-registers the listener; it attaches/detaches only when `active` flips.
 * Dialogs that early-return on a closed flag (`if (!open) return null`) should
 * call this before the early return with that flag as `active`.
 */
export function useEscapeKey(active: boolean, onClose: () => void) {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!active) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCloseRef.current();
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [active]);
}
