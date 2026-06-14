export type Sentiment = "positive" | "negative" | null;

const POSITIVE_TERMS = new Set([
  "long",
  "bullish",
  "true",
  "increasing",
  "positive",
  "yes",
  "buy",
]);

const NEGATIVE_TERMS = new Set([
  "short",
  "bearish",
  "false",
  "decreasing",
  "negative",
  "no",
  "sell",
]);

/** Columns that should not receive green/red sentiment coloring. */
export const NO_SENTIMENT_COLUMNS = new Set([
  "session",
  "entry tick",
  "exit tick",
  "tick",
  "ticks parsed",
  "pair rows",
  "sim trades",
  "formal trades",
  "adaptive trades",
  "pair",
  "interval",
  "parameter",
  "value",
  "rule",
  "condition",
  "entry class",
  "trigger",
  "exit reason",
  "status",
  "hold",
  "created",
  "volume $",
]);

/** Parse numeric strings like $+189.90, -12.5%, or +3.2. */
export function parseSignedNumber(text: string): number | null {
  let normalized = text.trim().replace(/,/g, "");
  if (!normalized) return null;

  for (const prefix of ["$", "€", "£"]) {
    if (normalized.startsWith(prefix)) {
      normalized = normalized.slice(prefix.length).trim();
      break;
    }
  }

  if (normalized.endsWith("%")) {
    normalized = normalized.slice(0, -1).trim();
  }

  const num = Number(normalized);
  if (normalized !== "" && !Number.isNaN(num)) return num;
  return null;
}

/** Map a cell/KPI value to bullish (positive) or bearish (negative) sentiment. */
export function getSentiment(value: unknown): Sentiment {
  if (value === true) return "positive";
  if (value === false) return "negative";

  if (typeof value === "number") {
    if (value > 0) return "positive";
    if (value < 0) return "negative";
    return null;
  }

  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (POSITIVE_TERMS.has(normalized)) return "positive";
    if (NEGATIVE_TERMS.has(normalized)) return "negative";

    const parsed = parseSignedNumber(value);
    if (parsed !== null) {
      if (parsed > 0) return "positive";
      if (parsed < 0) return "negative";
      return null;
    }
  }

  return null;
}

/** Whether a table column should skip sentiment coloring. */
export function shouldSkipSentimentColumn(column: string): boolean {
  return NO_SENTIMENT_COLUMNS.has(column.trim().toLowerCase());
}

/** Tailwind class for sentiment-colored text. */
export function sentimentClass(value: unknown, trend?: string | null): string {
  const fromTrend =
    trend === "positive" || trend === "up"
      ? "positive"
      : trend === "negative" || trend === "down"
        ? "negative"
        : null;

  const sentiment = fromTrend ?? getSentiment(value);
  if (sentiment === "positive") return "text-[var(--color-green)]";
  if (sentiment === "negative") return "text-[var(--color-red)]";
  return "";
}
