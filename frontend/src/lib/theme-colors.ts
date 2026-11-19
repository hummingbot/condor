type ThemeColors = {
  up: string;
  down: string;
  green: string;
  red: string;
  yellow: string;
  bg: string;
  grid: string;
  text: string;
  /** `--color-text-muted`: labels and secondary rows inside chart tooltips. */
  textMuted: string;
  /** `--color-border`: separator rules and chips inside chart tooltips. */
  border: string;
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
    bg: style.getPropertyValue("--chart-bg").trim() || "#0f1525",
    grid: style.getPropertyValue("--chart-grid").trim() || "#1c2541",
    text: style.getPropertyValue("--chart-text").trim() || "#6b7994",
    textMuted: style.getPropertyValue("--color-text-muted").trim() || "#6b7994",
    border: style.getPropertyValue("--color-border").trim() || "#1c2541",
  };
}

// Invalidate the cache whenever the theme changes. Theme switching toggles the
// [data-theme] / [data-colorblind] attributes on <html> (see hooks/useTheme.ts),
// so observe those signals.
if (typeof document !== "undefined") {
  const observer = new MutationObserver(() => {
    cachedColors = null;
  });
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-colorblind"] });
}

/** Read chart colors from CSS variables (respects light/dark and the colour-blind palette). */
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

/**
 * `color` at `alpha` opacity, whatever CSS syntax it is written in.
 *
 * Theme tokens are `hsl(...)` since the design refresh, while older chart
 * colours are hex or `rgba(...)`. A helper that understood only the latter
 * handed the theme's colours back unchanged, so a "dimmed" line stayed bright.
 * Anything it cannot parse is returned as is.
 */
export function withAlpha(color: string, alpha: number): string {
  const c = color.trim();
  const a = Math.max(0, Math.min(1, alpha));
  const hex = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(c);
  if (hex) {
    let h = hex[1];
    if (h.length <= 4) h = [...h].map((x) => x + x).join("");
    const byte = Math.round(a * 255)
      .toString(16)
      .padStart(2, "0");
    return `#${h.slice(0, 6)}${byte}`;
  }
  const fn = /^(rgb|hsl)a?\((.*)\)$/i.exec(c);
  if (fn) {
    // Drop any alpha already there, in either the comma or the slash syntax.
    const body = fn[2].split("/")[0].trim();
    const parts = body.includes(",")
      ? body.split(",").map((p) => p.trim())
      : body.split(/\s+/);
    if (parts.length < 3) return c;
    const [x, y, z] = parts;
    return `${fn[1].toLowerCase()}a(${x}, ${y}, ${z}, ${a})`;
  }
  return c;
}
