// ── Centralized Formatters ──

export function formatCurrency(val: number, symbol = "$") {
  if (Math.abs(val) >= 1_000_000) return symbol + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return symbol + (val / 1_000).toFixed(1) + "K";
  if (symbol === "$") {
    return val.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
    });
  }
  // Adaptive precision for small values (e.g. BTC)
  if (Math.abs(val) < 0.01 && val !== 0) return symbol + val.toPrecision(4);
  return symbol + val.toFixed(2);
}

export function formatCurrencyVolume(val: number, symbol = "$") {
  if (Math.abs(val) >= 1_000_000) return symbol + (val / 1_000_000).toFixed(1) + "M";
  if (Math.abs(val) >= 1_000) return symbol + (val / 1_000).toFixed(1) + "K";
  return symbol + val.toFixed(0);
}

export function formatCurrencyPnl(val: number, symbol = "$") {
  const prefix = val >= 0 ? "+" : "";
  return prefix + formatCurrency(val, symbol);
}

export function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

// Backward-compatible aliases (default to $)
export function formatUsd(val: number) {
  return formatCurrency(val);
}

export function formatVolume(val: number) {
  return formatCurrencyVolume(val);
}

export function formatPnl(val: number) {
  return formatCurrencyPnl(val);
}

export function formatAge(timestamp: number): string {
  if (!timestamp) return "\u2014";
  try {
    const now = Date.now();
    const diffMs = now - timestamp * 1000;
    if (diffMs < 0) return "\u2014";
    const days = Math.floor(diffMs / 86400000);
    const hours = Math.floor((diffMs % 86400000) / 3600000);
    if (days > 0) return `${days}d ${hours}h`;
    const mins = Math.floor((diffMs % 3600000) / 60000);
    if (hours > 0) return `${hours}h ${mins}m`;
    if (mins > 0) return `${mins}m`;
    return "<1m";
  } catch {
    return "\u2014";
  }
}

export function formatPrice(val: number): string {
  if (!val) return "\u2014";
  if (val >= 1000) return val.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (val >= 1) return val.toFixed(4);
  return val.toPrecision(4);
}

export function formatPct(val: number): string {
  if (!val) return "\u2014";
  return (val >= 0 ? "+" : "") + (val * 100).toFixed(2) + "%";
}

export function isExecutorActive(status: string) {
  return status === "active" || status === "running";
}
