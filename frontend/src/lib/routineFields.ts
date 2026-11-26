/** Words that read wrong title-cased: `db_path` should say "DB path". */
const ACRONYMS: Record<string, string> = {
  db: "DB", id: "ID", url: "URL", api: "API", lp: "LP", pnl: "PnL", usd: "USD",
  dex: "DEX", cex: "CEX", tp: "TP", sl: "SL", ms: "ms",
};

/** `db_filter` → "DB filter": the field's name, said as a label. */
export function humanizeFieldName(key: string): string {
  const words = key.split(/[_\s]+/).filter(Boolean).map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  if (!words.length) return key;
  const [first, ...rest] = words;
  return [first === first.toLowerCase() ? first.charAt(0).toUpperCase() + first.slice(1) : first, ...rest].join(" ");
}
