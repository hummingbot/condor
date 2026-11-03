import { useEffect, useRef } from "react";

/** Something a click can land in without counting as "outside". */
type Inside = HTMLElement | null | { current: HTMLElement | null };

/**
 * Close when a mousedown lands outside every element the menu considers its own.
 *
 * `mousedown` rather than `click` so a drag that starts outside dismisses, and
 * the containment test takes a list because a portalled menu's "inside" is two
 * disjoint subtrees — the trigger and the panel. Miss the panel and a mousedown
 * on a menu item would unmount it before the click that selects it ever lands.
 *
 * Callbacks and targets live in refs, so passing inline closures and a fresh
 * array every render never re-registers the listener; it binds only while
 * `open`, which is the drift this hook exists to stop repeating.
 */
export function useDismissOnOutsideClick(
  open: boolean,
  onClose: () => void,
  inside: Inside[],
) {
  const onCloseRef = useRef(onClose);
  const insideRef = useRef(inside);
  useEffect(() => {
    onCloseRef.current = onClose;
    insideRef.current = inside;
  });

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const hit = insideRef.current.some((i) => {
        const el = i && "current" in i ? i.current : i;
        return el ? el.contains(target) : false;
      });
      if (!hit) onCloseRef.current();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);
}
