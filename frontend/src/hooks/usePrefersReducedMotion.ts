import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

/** jsdom has no `matchMedia` at all — every call site falls back to "motion is
 *  fine" rather than throwing, so a component using this hook stays mountable
 *  in a test that never cared about the media query. */
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

/** Whether the operator asked their OS for less motion — the gate every
 *  screen transition in the hub import flow checks before animating. */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
