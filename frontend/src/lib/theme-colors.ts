/** Read chart colors from CSS variables (respects dark/light/colorblind theme). */
export function getThemeColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    up: style.getPropertyValue("--chart-up").trim() || "#22c55e",
    down: style.getPropertyValue("--chart-down").trim() || "#ef4444",
    green: style.getPropertyValue("--color-green").trim() || "#22c55e",
    red: style.getPropertyValue("--color-red").trim() || "#ef4444",
    yellow: style.getPropertyValue("--color-yellow").trim() || "#eab308",
  };
}

/** Return the up or down color based on a numeric value. */
export function pnlColor(value: number): string {
  const { up, down } = getThemeColors();
  return value >= 0 ? up : down;
}

/** Return the up or down color based on buy/sell side. */
export function sideColor(side: string): string {
  const { up, down } = getThemeColors();
  return side === "buy" ? up : down;
}
