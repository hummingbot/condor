import { useEffect, useRef } from "react";

/**
 * One registered Escape handler. `fire` reads the caller's latest closure;
 * `when` lets a handler stand down for a keypress that is not its to take, in
 * which case the key falls through to the handler below it.
 */
interface EscapeEntry {
  fire: () => void;
  when?: (e: KeyboardEvent) => boolean;
}

/**
 * Every active Escape handler, innermost last (FU-05).
 *
 * Each caller used to add its own `window` listener, with no ordering between
 * them — so one keypress closed every open overlay at once (a discard dialog
 * and the editor under it), and the chat's "Esc stops the answer" listener
 * fired for an Escape that was only meant to close a menu. One listener and a
 * stack make the key mean one thing: the most recently opened handler takes
 * it, and nothing underneath hears it.
 *
 * Order is activation order. Two handlers activated in the same commit are
 * ordered by React's effect order (child before parent); no caller opens an
 * overlay and its own child overlay in one commit today.
 */
const stack: EscapeEntry[] = [];

function onKeyDown(e: KeyboardEvent) {
  if (e.key !== "Escape") return;
  for (let i = stack.length - 1; i >= 0; i--) {
    const entry = stack[i];
    if (entry.when && !entry.when(e)) continue;
    entry.fire();
    e.preventDefault();
    return;
  }
}

function register(entry: EscapeEntry, base: boolean): () => void {
  if (stack.length === 0) window.addEventListener("keydown", onKeyDown);
  if (base) stack.unshift(entry);
  else stack.push(entry);
  return () => {
    const i = stack.indexOf(entry);
    if (i >= 0) stack.splice(i, 1);
    if (stack.length === 0) window.removeEventListener("keydown", onKeyDown);
  };
}

export interface EscapeKeyOptions {
  /**
   * Register underneath every overlay instead of on top. For a page-level
   * meaning of Escape — the chat's "stop the answer" — that must never win
   * over a menu or dialog, however late it was registered.
   */
  base?: boolean;
  /**
   * Asked per keypress; `false` passes the key to the next handler down. Read
   * through a ref, so an inline closure is fine.
   */
  when?: (e: KeyboardEvent) => boolean;
}

/**
 * Close-on-Escape for hand-rolled modal overlays. While `active` is true,
 * `Escape` invokes `onClose` and calls `preventDefault()`, exactly like the
 * dialogs' backdrop/Cancel paths expect (see CORR-077) — but only for the
 * innermost active caller: handlers form a stack (see above), so Escape closes
 * the topmost overlay and nothing else.
 *
 * The callback is kept in a ref so passing an inline closure never
 * re-registers the handler; it registers/unregisters only when `active` flips.
 * Dialogs that early-return on a closed flag (`if (!open) return null`) should
 * call this before the early return with that flag as `active`.
 */
export function useEscapeKey(
  active: boolean,
  onClose: () => void,
  options?: EscapeKeyOptions,
) {
  const onCloseRef = useRef(onClose);
  const whenRef = useRef(options?.when);
  useEffect(() => {
    onCloseRef.current = onClose;
    whenRef.current = options?.when;
  });

  const base = !!options?.base;
  useEffect(() => {
    if (!active) return;
    return register(
      {
        fire: () => onCloseRef.current(),
        when: (e) => whenRef.current?.(e) ?? true,
      },
      base,
    );
  }, [active, base]);
}
