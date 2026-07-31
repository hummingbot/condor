import { Brain, ChevronUp, ChevronDown, Infinity as InfinityIcon, Zap } from "lucide-react";
import { useMemo, useState } from "react";

import { formatRelativeTime } from "@/lib/formatters";

export interface RoutineRow {
  /** Canonical routine name (used as key and to open the report browser). */
  name: string;
  /** Human display name. */
  displayName: string;
  description: string;
  isAgent: boolean;
  agentName: string | null;
  isContinuous: boolean;
  category: string;
  reportCount: number;
  /** Epoch seconds of the source file's last modification, or null. */
  lastModified: number | null;
  /** Epoch seconds of the most recent execution/report, or null. */
  lastExecuted: number | null;
  hasActive: boolean;
}

type SortKey = "name" | "type" | "category" | "reports" | "lastExecuted" | "lastModified";
type SortDir = "asc" | "desc";

// Numeric sort keys default to descending (most recent / highest first) on first click.
const NUMERIC_KEYS: ReadonlySet<SortKey> = new Set(["reports", "lastExecuted", "lastModified"]);

function compareRows(a: RoutineRow, b: RoutineRow, key: SortKey): number {
  switch (key) {
    case "name":
      return a.displayName.localeCompare(b.displayName);
    case "type":
      // Group by source then by continuous flag.
      return (
        Number(a.isAgent) - Number(b.isAgent) ||
        Number(a.isContinuous) - Number(b.isContinuous)
      );
    case "category":
      return a.category.localeCompare(b.category);
    case "reports":
      return a.reportCount - b.reportCount;
    case "lastExecuted":
      return (a.lastExecuted ?? -Infinity) - (b.lastExecuted ?? -Infinity);
    case "lastModified":
      return (a.lastModified ?? -Infinity) - (b.lastModified ?? -Infinity);
    default:
      return 0;
  }
}

function SortHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = currentKey === sortKey;
  return (
    <th
      className={`px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)] cursor-pointer select-none hover:text-[var(--color-text)] transition-colors ${
        align === "right" ? "text-right" : "text-left"
      }`}
      onClick={() => onSort(sortKey)}
    >
      <div className={`flex items-center gap-1 ${align === "right" ? "justify-end" : ""}`}>
        {label}
        {active ? (
          currentDir === "asc" ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )
        ) : (
          <span className="w-3" />
        )}
      </div>
    </th>
  );
}

export function RoutineTable({
  rows,
  onOpen,
}: {
  rows: RoutineRow[];
  onOpen: (name: string) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("lastExecuted");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(NUMERIC_KEYS.has(key) ? "desc" : "asc");
    }
  };

  const sorted = useMemo(() => {
    const out = [...rows].sort((a, b) => compareRows(a, b, sortKey));
    if (sortDir === "desc") out.reverse();
    return out;
  }, [rows, sortKey, sortDir]);

  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
      <table className="w-full border-collapse text-sm">
        <thead className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
          <tr>
            <SortHeader label="Name" sortKey="name" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
            <SortHeader label="Type" sortKey="type" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
            <SortHeader label="Category" sortKey="category" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} />
            <SortHeader label="Reports" sortKey="reports" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="right" />
            <SortHeader label="Last Executed" sortKey="lastExecuted" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="right" />
            <SortHeader label="Last Modified" sortKey="lastModified" currentKey={sortKey} currentDir={sortDir} onSort={handleSort} align="right" />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr
              key={r.name}
              onClick={() => onOpen(r.name)}
              className="cursor-pointer border-b border-[var(--color-border)] last:border-0 transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <td className="px-3 py-2">
                <div className="flex items-center gap-1.5">
                  {r.hasActive && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
                  )}
                  <span className="font-medium text-[var(--color-text)]">{r.displayName}</span>
                  {r.isAgent && r.agentName && (
                    <span className="inline-flex items-center gap-0.5 rounded bg-purple-500/10 px-1 py-0.5 text-[8px] font-bold uppercase text-purple-400">
                      <Brain className="h-2 w-2" />
                      {r.agentName}
                    </span>
                  )}
                </div>
                {r.description && (
                  <p className="mt-0.5 truncate text-[10px] text-[var(--color-text-muted)] max-w-[28rem]">
                    {r.description}
                  </p>
                )}
              </td>
              <td className="px-3 py-2 whitespace-nowrap">
                <span className="inline-flex items-center gap-1 text-[11px] text-[var(--color-text-muted)]">
                  {r.isContinuous ? (
                    <>
                      <InfinityIcon className="h-3 w-3" />
                      Continuous
                    </>
                  ) : (
                    <>
                      <Zap className="h-3 w-3" />
                      One-shot
                    </>
                  )}
                </span>
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-[11px] text-[var(--color-text-muted)]">
                {r.category}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-[11px] text-[var(--color-text-muted)]">
                {r.reportCount > 0 ? r.reportCount : "—"}
              </td>
              <td className="px-3 py-2 text-right whitespace-nowrap text-[11px] text-[var(--color-text-muted)]">
                {formatRelativeTime(r.lastExecuted, "—")}
              </td>
              <td className="px-3 py-2 text-right whitespace-nowrap text-[11px] text-[var(--color-text-muted)]">
                {formatRelativeTime(r.lastModified, "—")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
