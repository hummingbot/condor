import { useSyncExternalStore } from "react";

/** Below Tailwind's `md` (48rem): the width where pages stop sitting panes side
 *  by side. Kept in step with the `md:` classes that do the same in CSS. */
const QUERY = "(max-width: 47.99rem)";

/** jsdom has no `matchMedia`; tests that never asked get the desktop answer. */
function supported(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function";
}

function getSnapshot(): boolean {
  if (!supported()) return false;
  return window.matchMedia(QUERY).matches;
}

function subscribe(listener: () => void): () => void {
  if (!supported()) return () => {};
  const mq = window.matchMedia(QUERY);
  mq.addEventListener("change", listener);
  return () => mq.removeEventListener("change", listener);
}

/** Whether the window is phone-width, for layout a CSS class cannot express —
 *  a component that renders different content there, not just hides some. */
export function useIsPhone(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
