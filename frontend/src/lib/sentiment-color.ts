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

    const num = Number(normalized.replace(/,/g, ""));
    if (normalized !== "" && !Number.isNaN(num)) {
      if (num > 0) return "positive";
      if (num < 0) return "negative";
      return null;
    }

    if (normalized.startsWith("+")) return "positive";
    if (/^-\d/.test(normalized)) return "negative";
  }

  return null;
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
