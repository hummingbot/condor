import { useCallback, useSyncExternalStore } from "react";

export type Theme = "dark" | "light" | "colorblind";

const THEMES: Theme[] = ["dark", "light", "colorblind"];
const STORAGE_KEY = "condor_theme";

function getSystemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getStoredTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
  if (stored && THEMES.includes(stored)) return stored;
  return getSystemTheme();
}

// Simple external store for theme
let currentTheme: Theme = getStoredTheme();
const listeners = new Set<() => void>();

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): Theme {
  return currentTheme;
}

// Apply initial theme immediately
applyTheme(currentTheme);

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot);

  const setTheme = useCallback((t: Theme) => {
    currentTheme = t;
    localStorage.setItem(STORAGE_KEY, t);
    applyTheme(t);
    listeners.forEach((l) => l());
  }, []);

  const toggleTheme = useCallback(() => {
    const idx = THEMES.indexOf(currentTheme);
    setTheme(THEMES[(idx + 1) % THEMES.length]);
  }, [setTheme]);

  return { theme, setTheme, toggleTheme };
}
