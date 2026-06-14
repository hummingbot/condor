import {
  getSentiment,
  parseSignedNumber,
  shouldSkipSentimentColumn,
} from "./sentiment-color";

const POSITIVE_CLASS = "condor-report-positive";
const NEGATIVE_CLASS = "condor-report-negative";

function parseCellValue(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "True") return true;
  if (trimmed === "False") return false;
  const parsed = parseSignedNumber(trimmed);
  if (parsed !== null) return parsed;
  return trimmed;
}

function applySentimentClass(el: Element, value: unknown): void {
  if (el.classList.contains("positive") || el.classList.contains("negative")) {
    return;
  }
  el.classList.remove(POSITIVE_CLASS, NEGATIVE_CLASS);
  const sentiment = getSentiment(value);
  if (sentiment === "positive") el.classList.add(POSITIVE_CLASS);
  else if (sentiment === "negative") el.classList.add(NEGATIVE_CLASS);
}

function colorizeTableCells(table: HTMLTableElement): void {
  const headers = Array.from(table.querySelectorAll("thead th")).map(
    (th) => th.textContent ?? "",
  );

  table.querySelectorAll("tbody tr").forEach((row) => {
    row.querySelectorAll("td").forEach((cell, colIndex) => {
      const column = headers[colIndex] ?? "";
      if (shouldSkipSentimentColumn(column)) return;
      applySentimentClass(cell, parseCellValue(cell.textContent ?? ""));
    });
  });
}

/** Colorize saved report HTML (works for reports generated before sentiment styling). */
export function colorizeReportDocument(doc: Document): void {
  if (!doc.getElementById("condor-sentiment-styles")) {
    const style = doc.createElement("style");
    style.id = "condor-sentiment-styles";
    style.textContent = `
      .${POSITIVE_CLASS} { color: var(--green, #3fb950) !important; }
      .${NEGATIVE_CLASS} { color: var(--red, #f85149) !important; }
    `;
    doc.head.appendChild(style);
  }

  doc.querySelectorAll(".section-table table, table").forEach((table) => {
    colorizeTableCells(table as HTMLTableElement);
  });

  doc.querySelectorAll(".kpi-card .value").forEach((el) => {
    applySentimentClass(el, parseCellValue(el.textContent ?? ""));
  });
}
