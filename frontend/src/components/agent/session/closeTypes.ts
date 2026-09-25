import type { AgentPerformance } from "@/lib/api";

/** "CloseType.EARLY_STOP" → "early stop" */
export function prettyCloseType(raw: string): string {
  return raw.replace(/^CloseType\./, "").replace(/_/g, " ").toLowerCase();
}

/** Total closes and a readable breakdown from the raw close-type counts. */
export function closeSummary(perf?: AgentPerformance | null): { total: number; label: string } {
  const counts = perf?.close_type_counts ?? {};
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  return {
    total: entries.reduce((sum, [, n]) => sum + n, 0),
    label: entries.map(([k, n]) => `${prettyCloseType(k)} ×${n}`).join(", "),
  };
}
