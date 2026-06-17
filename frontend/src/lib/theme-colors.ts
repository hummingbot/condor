type ThemeColors = {
  up: string;
  down: string;
  green: string;
  red: string;
  yellow: string;
};

// Cached theme colors — computed once and refreshed only when the theme changes.
// getComputedStyle() forces a synchronous style recalc, so caching avoids reflows
// in render loops (Portfolio token charts) and hot paths (TradeChart crosshair move).
let cachedColors: ThemeColors | null = null;

function readThemeColors(): ThemeColors {
  const style = getComputedStyle(document.documentElement);
  return {
    up: style.getPropertyValue("--chart-up").trim() || "#22c55e",
    down: style.getPropertyValue("--chart-down").trim() || "#ef4444",
    green: style.getPropertyValue("--color-green").trim() || "#22c55e",
    red: style.getPropertyValue("--color-red").trim() || "#ef4444",
    yellow: style.getPropertyValue("--color-yellow").trim() || "#eab308",
  };
}

// Invalidate the cache whenever the theme changes. Theme switching toggles the
// [data-theme] attribute on <html> (see hooks/useTheme.ts), so observe that signal.
if (typeof document !== "undefined") {
  const observer = new MutationObserver(() => {
    cachedColors = null;
  });
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
}

/** Read chart colors from CSS variables (respects dark/light/colorblind theme). */
export function getThemeColors(): ThemeColors {
  if (cachedColors === null) cachedColors = readThemeColors();
  return cachedColors;
}

/** Return the up or down hex color based on a numeric value (for canvas/charts). */
export function pnlHexColor(value: number): string {
  const { up, down } = getThemeColors();
  return value >= 0 ? up : down;
}

/** Return the up or down color based on buy/sell side. */
export function sideColor(side: string): string {
  const { up, down } = getThemeColors();
  return side === "buy" ? up : down;
}
