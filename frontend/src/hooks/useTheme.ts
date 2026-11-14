import { useCallback, useSyncExternalStore } from "react";

import { COLORBLIND_KEY, THEME_KEY } from "@/lib/sessionState";

/**
 * Appearance is two independent choices:
 *
 * - `theme` — light or dark. The top-bar toggle flips this and nothing else.
 * - `colorblind` — an accessibility palette (blue/orange instead of
 *   green/red, blue instead of gold) applied *on top of* either theme. It is
 *   chosen in Settings → Appearance, not cycled through from the top bar,
 *   where it used to read as a third theme.
 *
 * On <html>: `data-theme="light|dark"` and, when on, `data-colorblind`.
 */
export type Theme = "dark" | "light";

const THEMES: Theme[] = ["dark", "light"];

function getSystemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readStored(): { theme: Theme; colorblind: boolean } {
  const stored = localStorage.getItem(THEME_KEY);
  // Before appearance was split, colour-blind was a third, dark-based theme.
  // Carry that choice over as dark + colour-blind rather than dropping it.
  if (stored === "colorblind") {
    localStorage.setItem(THEME_KEY, "dark");
    localStorage.setItem(COLORBLIND_KEY, "1");
    return { theme: "dark", colorblind: true };
  }
  const theme = stored && (THEMES as string[]).includes(stored) ? (stored as Theme) : getSystemTheme();
  return { theme, colorblind: localStorage.getItem(COLORBLIND_KEY) === "1" };
}

type State = { theme: Theme; colorblind: boolean };

let current: State = readStored();
const listeners = new Set<() => void>();

function apply({ theme, colorblind }: State) {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  if (colorblind) root.setAttribute("data-colorblind", "");
  else root.removeAttribute("data-colorblind");
}

function set(next: State) {
  current = next;
  apply(next);
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): State {
  return current;
}

// Apply initial appearance immediately
apply(current);

export function useTheme() {
  const { theme, colorblind } = useSyncExternalStore(subscribe, getSnapshot);

  const setTheme = useCallback((t: Theme) => {
    localStorage.setItem(THEME_KEY, t);
    set({ ...current, theme: t });
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(current.theme === "dark" ? "light" : "dark");
  }, [setTheme]);

  const setColorblind = useCallback((on: boolean) => {
    if (on) localStorage.setItem(COLORBLIND_KEY, "1");
    else localStorage.removeItem(COLORBLIND_KEY);
    set({ ...current, colorblind: on });
  }, []);

  return { theme, setTheme, toggleTheme, colorblind, setColorblind };
}
